from typing import get_args

from fastapi.params import Depends

from mongars.api.dependencies import SessionDependency, get_session


def test_database_transaction_finishes_before_response_delivery() -> None:
    """A successful mutation must be visible when its HTTP response reaches the client."""

    metadata = get_args(SessionDependency)[1:]
    assert len(metadata) == 1
    dependency = metadata[0]
    assert isinstance(dependency, Depends)
    assert dependency.dependency is get_session
    assert dependency.scope == "function"
