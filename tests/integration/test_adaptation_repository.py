from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from mongars.adaptation.feedback import PreferenceFeedback
from mongars.adaptation.mimicry import propose_profile_delta
from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.adaptation.repository import (
    FeedbackIdentityConflict,
    PersonalityProfileConflict,
    PersonalityRepository,
)
from mongars.config import Environment, Settings
from mongars.db.session import Database

_RAW_DATABASE_URL = os.getenv("MONGARS_TEST_DATABASE_URL", "").strip()
if not _RAW_DATABASE_URL:
    pytest.skip(
        "MONGARS_TEST_DATABASE_URL is required for PostgreSQL integration tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


def _psycopg_url(value: str) -> str:
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise ValueError("MONGARS_TEST_DATABASE_URL must target PostgreSQL")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


DATABASE_URL = _psycopg_url(_RAW_DATABASE_URL)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> Iterator[None]:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    previous_url = os.environ.get("MONGARS_DATABASE_URL")
    os.environ["MONGARS_DATABASE_URL"] = DATABASE_URL
    try:
        command.upgrade(config, "head")
        yield
    finally:
        if previous_url is None:
            os.environ.pop("MONGARS_DATABASE_URL", None)
        else:
            os.environ["MONGARS_DATABASE_URL"] = previous_url


async def _clean_owner(database: Database, owner_id: str) -> None:
    async with database.session_factory() as session, session.begin():
        await session.execute(
            delete(PersonalityProfileRevisionRecord).where(
                PersonalityProfileRevisionRecord.owner_id == owner_id
            )
        )
        await session.execute(
            delete(PersonalityProfileRecord).where(PersonalityProfileRecord.owner_id == owner_id)
        )
        await session.execute(
            delete(ExplicitFeedbackRecord).where(ExplicitFeedbackRecord.owner_id == owner_id)
        )


@pytest.mark.asyncio
async def test_feedback_is_idempotent_and_reviewed_profile_application_is_atomic() -> None:
    owner_id = f"adaptation-{uuid4().hex}"
    settings = Settings(environment=Environment.TEST, database_url=DATABASE_URL)
    database = Database(settings)
    feedback_id = uuid4()
    feedback = PreferenceFeedback(
        feedback_id=feedback_id,
        dimension="brevity",
        desired_value=0.8,
    )
    task_id = uuid4()

    try:
        async with database.session_factory() as session, session.begin():
            repository = PersonalityRepository(session)
            first = await repository.record_feedback(owner_id=owner_id, feedback=feedback)
            duplicate = await repository.record_feedback(owner_id=owner_id, feedback=feedback)
            assert first.created is True
            assert duplicate.created is False
            assert first.feedback_digest == duplicate.feedback_digest

            with pytest.raises(FeedbackIdentityConflict):
                await repository.record_feedback(
                    owner_id=owner_id,
                    feedback=PreferenceFeedback(
                        feedback_id=feedback_id,
                        dimension="brevity",
                        desired_value=0.2,
                    ),
                )

            current = await repository.current_snapshot(owner_id=owner_id)
            proposal = propose_profile_delta(current, feedback)
            forged_base = propose_profile_delta(
                current,
                PreferenceFeedback(
                    feedback_id=feedback_id,
                    dimension="brevity",
                    desired_value=0.2,
                ),
            )
            assert proposal is not None
            assert forged_base is not None
            forged = replace(
                forged_base,
                feedback_digest=feedback.feedback_digest,
            )
            with pytest.raises(PersonalityProfileConflict, match="persisted explicit preference"):
                await repository.apply_proposal(
                    owner_id=owner_id,
                    proposal=forged,
                    task_id=uuid4(),
                )

            application = await repository.apply_proposal(
                owner_id=owner_id,
                proposal=proposal,
                task_id=task_id,
            )
            assert application.applied is True
            assert application.snapshot.revision == 1

        async with database.session_factory() as session, session.begin():
            repository = PersonalityRepository(session)
            current = await repository.current_snapshot(owner_id=owner_id)
            history = await repository.revision_history(owner_id=owner_id)
            replay = await repository.apply_proposal(
                owner_id=owner_id,
                proposal=proposal,
                task_id=task_id,
            )

            assert current == proposal.target_snapshot
            assert len(history) == 1
            assert history[0].snapshot == current
            assert history[0].task_id == task_id
            assert replay.applied is False
            assert replay.snapshot == current
    finally:
        await _clean_owner(database, owner_id)
        await database.close()


@pytest.mark.asyncio
async def test_stale_profile_proposal_fails_closed_and_owner_state_is_isolated() -> None:
    owner_a = f"adaptation-a-{uuid4().hex}"
    owner_b = f"adaptation-b-{uuid4().hex}"
    settings = Settings(environment=Environment.TEST, database_url=DATABASE_URL)
    database = Database(settings)
    feedback_a = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="directness",
        desired_value=0.9,
    )
    stale_feedback = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="technical_depth",
        desired_value=0.7,
    )

    try:
        async with database.session_factory() as session, session.begin():
            repository = PersonalityRepository(session)
            initial = await repository.current_snapshot(owner_id=owner_a)
            proposal_a = propose_profile_delta(initial, feedback_a)
            stale_proposal = propose_profile_delta(initial, stale_feedback)
            assert proposal_a is not None
            assert stale_proposal is not None
            await repository.record_feedback(owner_id=owner_a, feedback=feedback_a)
            await repository.record_feedback(owner_id=owner_a, feedback=stale_feedback)
            await repository.apply_proposal(
                owner_id=owner_a,
                proposal=proposal_a,
                task_id=uuid4(),
            )
            with pytest.raises(PersonalityProfileConflict, match="revision changed"):
                await repository.apply_proposal(
                    owner_id=owner_a,
                    proposal=stale_proposal,
                    task_id=uuid4(),
                )

            assert (await repository.current_snapshot(owner_id=owner_b)).revision == 0
            assert await repository.revision_history(owner_id=owner_b) == ()
    finally:
        await _clean_owner(database, owner_a)
        await _clean_owner(database, owner_b)
        await database.close()
