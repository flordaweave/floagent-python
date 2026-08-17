from datetime import datetime, timedelta, timezone

import pytest

from floagent_sdk.models import FloAgentSession, SessionValidationError


def payload(expires_at):
    return {
        "profile": {"profile_id": "profile-1"},
        "access_token": "access",
        "refresh_token": "refresh",
        "access_token_expires_at": expires_at,
    }


def test_session_round_trip_and_expiry_check():
    now = datetime.now(timezone.utc)
    session = FloAgentSession.from_mapping(
        payload((now + timedelta(seconds=20)).isoformat()),
        fallback_api_base_url="https://api.example.com",
    )

    assert session.needs_refresh(now=now, leeway_seconds=30)
    assert FloAgentSession.from_dict(session.to_dict()) == session


def test_session_requires_timezone_on_expiry():
    with pytest.raises(SessionValidationError, match="timezone"):
        FloAgentSession.from_mapping(
            payload("2026-08-16T12:00:00"),
            fallback_api_base_url="https://api.example.com",
        )


def test_session_requires_profile_mapping():
    invalid = payload("2026-08-16T12:00:00+00:00")
    invalid["profile"] = "profile-1"
    with pytest.raises(SessionValidationError, match="profile"):
        FloAgentSession.from_mapping(
            invalid,
            fallback_api_base_url="https://api.example.com",
        )
