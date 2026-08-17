import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from floagent_sdk import HandoffClient
from floagent_sdk.streamlit import (
    POST_SWITCH_QUERY_KEY,
    SESSION_KEY,
    StreamlitHandoff,
    parse_redirect_target,
)


class Rerun(Exception):
    pass


class SwitchPage(Exception):
    def __init__(self, page):
        self.page = page


class Stop(Exception):
    pass


class QueryParams(dict):
    def get_all(self, key):
        value = self.get(key)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def from_dict(self, values):
        self.clear()
        self.update(values)


class Sidebar:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeStreamlit(SimpleNamespace):
    def __init__(self, query):
        super().__init__(
            query_params=QueryParams(query),
            session_state={},
            sidebar=Sidebar(),
            errors=[],
        )

    def rerun(self):
        raise Rerun

    def switch_page(self, page):
        raise SwitchPage(page)

    def stop(self):
        raise Stop

    def error(self, message):
        self.errors.append(message)

    def caption(self, message):
        return None

    def button(self, label, key):
        return False


class FakeResponse:
    def __init__(self):
        self.payload = json.dumps(
            {
                "profile": {
                    "profile_id": "profile-1",
                    "display_name": "Example User",
                },
                "access_token": "access",
                "refresh_token": "refresh",
                "access_token_expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
                "service_endpoints": {
                    "api_base_url": "https://api.example.com"
                },
            }
        ).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload


def test_parse_redirect_rejects_external_url_and_filters_auth_query():
    assert parse_redirect_target("https://attacker.example/admin").path == "/"
    target = parse_redirect_target(
        "/admin?task_id=task-9&token=not-a-handoff-token&tag=a&tag=b"
    )
    assert target.path == "/admin"
    assert target.query == {"task_id": "task-9", "tag": ["a", "b"]}


def test_handoff_exchange_cleans_url_switches_page_and_restores_query(monkeypatch):
    st = FakeStreamlit(
        {
            "token": "handoff-token",
            "api_base_url": "https://api.example.com",
            "redirect": "/admin?task_id=task-9",
        }
    )
    monkeypatch.setitem(sys.modules, "streamlit", st)
    client = HandoffClient(
        "https://api.example.com",
        opener=lambda request, timeout: FakeResponse(),
    )
    auth = StreamlitHandoff(
        client,
        redirect_pages={"/": "home-page", "/admin": "admin-page"},
    )

    with pytest.raises(Rerun):
        auth.require_session()
    assert st.query_params == {}
    assert SESSION_KEY in st.session_state

    with pytest.raises(SwitchPage) as switched:
        auth.require_session()
    assert switched.value.page == "admin-page"
    assert st.session_state[POST_SWITCH_QUERY_KEY] == {"task_id": "task-9"}

    session = auth.require_session()
    assert session.profile["profile_id"] == "profile-1"
    assert st.query_params == {"task_id": "task-9"}


def test_missing_handoff_stops_with_actionable_message(monkeypatch):
    st = FakeStreamlit({})
    monkeypatch.setitem(sys.modules, "streamlit", st)
    auth = StreamlitHandoff(
        HandoffClient("https://api.example.com"),
        redirect_pages={"/": "home-page"},
    )

    with pytest.raises(Stop):
        auth.require_session()
    assert st.errors == ["Open this app from FloAgent to sign in."]


def test_repeated_handoff_token_is_rejected_without_network(monkeypatch):
    st = FakeStreamlit({"token": ["one", "two"]})
    monkeypatch.setitem(sys.modules, "streamlit", st)
    auth = StreamlitHandoff(
        HandoffClient("https://api.example.com"),
        redirect_pages={"/": "home-page"},
    )

    with pytest.raises(Rerun):
        auth.require_session()
    assert st.query_params == {}
    assert SESSION_KEY not in st.session_state
