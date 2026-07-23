from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from mongars.adaptation.feedback import PreferenceFeedback
from mongars.adaptation.mimicry import propose_profile_delta
from mongars.adaptation.models import ExplicitFeedbackRecord, PersonalityProfileRecord, PersonalityProfileRevisionRecord
from mongars.adaptation.repository import PersonalityRepository
from mongars.config import Environment, Settings
from mongars.db.session import Database
from mongars.main import create_app

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
def migrated_database() -> None:
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
async def test_adaptation_reset_export_delete_api_control_paths() -> None:
    owner_id = f"adapt-api-{uuid4().hex}"
    token = uuid4().hex
    settings = Settings(
        environment=Environment.TEST,
        owner_id=owner_id,
        api_token=SecretStr(token),
        database_url=DATABASE_URL,
    )
    database = Database(settings)
    application = create_app(settings=settings, database=database)
    transport = httpx.ASGITransport(app=application)
    feedback_id = uuid4()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with database.session_factory() as session, session.begin():
            repository = PersonalityRepository(session)
            feedback = PreferenceFeedback(
                feedback_id=feedback_id,
                dimension="humor",
                desired_value=0.35,
            )
            await repository.record_feedback(owner_id=owner_id, feedback=feedback)
            proposal = propose_profile_delta(None, feedback)
            assert proposal is not None
            await repository.apply_proposal(owner_id=owner_id, proposal=proposal, task_id=uuid4())

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthorized_current = await client.get("/v1/adaptation/personality/current")
            assert unauthorized_current.status_code == 401

            export_response = await client.get(
                "/v1/adaptation/personality/export",
                headers=headers,
            )
            assert export_response.status_code == 200
            exported = export_response.json()
            assert exported["current"]["revision"] == 1
            assert exported["current"]["source"] == "explicit_feedback"
            assert len(exported["history"]) == 1

            repeat_feedback = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "preference",
                    "feedback_id": str(feedback_id),
                    "dimension": "humor",
                    "desired_value": 0.35,
                },
            )
            assert repeat_feedback.status_code == 202
            assert repeat_feedback.json()["created"] is False

            reset_response = await client.post(
                "/v1/adaptation/personality/reset",
                headers=headers,
            )
            assert reset_response.status_code == 200
            assert reset_response.json()["revision"] == 0
            assert reset_response.json()["source"] == "default"

            reset_again = await client.post(
                "/v1/adaptation/personality/reset",
                headers=headers,
            )
            assert reset_again.status_code == 200
            assert reset_again.json()["revision"] == 0

            reset_current = await client.get(
                "/v1/adaptation/personality/current",
                headers=headers,
            )
            assert reset_current.status_code == 200
            assert reset_current.json()["revision"] == 0

            reset_export = await client.get(
                "/v1/adaptation/personality/export",
                headers=headers,
            )
            assert reset_export.status_code == 200
            assert reset_export.json()["current"]["revision"] == 0
            assert reset_export.json()["history"] == []

            delete_response = await client.delete(
                "/v1/adaptation/personality",
                headers=headers,
            )
            assert delete_response.status_code == 204

            post_delete_feedback = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "preference",
                    "feedback_id": str(feedback_id),
                    "dimension": "humor",
                    "desired_value": 0.35,
                },
            )
            assert post_delete_feedback.status_code == 202
            assert post_delete_feedback.json()["created"] is True
    finally:
        await _clean_owner(database, owner_id)
        await database.close()
