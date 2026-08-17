import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.error import HTTPError

import pytest

from floagent_sdk import HandoffClient, HandoffError, __version__
from floagent_sdk.client import USER_AGENT, normalize_api_base_url


def session_payload(*, api_base_url="https://api.example.com", expires_in=300):
    return {
        "profile": {
            "profile_id": "profile-1",
            "display_name": "Example User",
        },
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat(),
        "pending_link": False,
        "service_endpoints": {
            "api_base_url": api_base_url,
            "web_base_url": "https://app.example.com",
        },
    }


def test_user_agent_contains_package_version():
    assert USER_AGENT == f"floagent-sdk-python/{__version__}"


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload


def test_exchange_posts_token_and_parses_session():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(session_payload())

    client = HandoffClient("https://api.example.com/", opener=opener)
    session = client.exchange(
        " one-time-token ",
        api_base_url="https://api.example.com",
    )

    request, timeout = requests[0]
    assert request.full_url == (
        "https://api.example.com/auth/login/handoff/exchange"
    )
    assert json.loads(request.data) == {"token": "one-time-token"}
    assert timeout == 10
    assert session.profile["profile_id"] == "profile-1"
    assert session.service_endpoints.api_base_url == "https://api.example.com"


def test_refresh_uses_discovered_session_endpoint():
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse(session_payload())

    client = HandoffClient("https://api.example.com", opener=opener)
    session = client.exchange("token")
    refreshed = client.refresh(session)

    assert requests[1].full_url.endswith("/auth/token/refresh")
    assert json.loads(requests[1].data) == {"refresh_token": "refresh-token"}
    assert refreshed.access_token == "access-token"


def test_rejects_unapproved_discovered_api_url_before_network_call():
    called = False

    def opener(request, timeout):
        nonlocal called
        called = True
        return FakeResponse(session_payload())

    client = HandoffClient("https://api.example.com", opener=opener)
    with pytest.raises(HandoffError, match="not allowed"):
        client.exchange("token", api_base_url="https://attacker.example")
    assert called is False


def test_rejects_unapproved_endpoint_in_exchange_response():
    client = HandoffClient(
        "https://api.example.com",
        opener=lambda request, timeout: FakeResponse(
            session_payload(api_base_url="https://attacker.example")
        ),
    )
    with pytest.raises(HandoffError, match="invalid session response"):
        client.exchange("token")


def test_http_auth_failure_has_safe_user_message_and_code():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            BytesIO(b'{"code":"web_login_handoff_reused"}'),
        )

    client = HandoffClient("https://api.example.com", opener=opener)
    with pytest.raises(HandoffError) as captured:
        client.exchange("secret-token")

    assert captured.value.status == 409
    assert captured.value.code == "web_login_handoff_reused"
    assert "secret-token" not in str(captured.value)
    assert "expired" in captured.value.user_message


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://user:password@api.example.com",
        "https://api.example.com?target=other",
        "file:///tmp/socket",
        "",
    ],
)
def test_api_base_url_rejects_unsafe_values(url):
    with pytest.raises(ValueError):
        normalize_api_base_url(url)


def test_api_base_url_allows_http_localhost_for_development():
    assert normalize_api_base_url("http://localhost:3000/") == (
        "http://localhost:3000"
    )


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_client_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout_seconds"):
        HandoffClient("https://api.example.com", timeout_seconds=timeout)
