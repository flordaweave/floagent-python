"""Streamlit adapter for FloAgent handoff authentication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .client import HandoffClient, HandoffError
from .models import FloAgentSession, SessionValidationError


SESSION_KEY = "_floagent_session"
ERROR_KEY = "_floagent_auth_error"
REDIRECT_KEY = "_floagent_auth_redirect"
POST_SWITCH_QUERY_KEY = "_floagent_post_switch_query"
HANDOFF_QUERY_KEYS = frozenset({"token", "api_base_url", "redirect"})


@dataclass(frozen=True)
class RedirectTarget:
    path: str
    query: dict[str, str | list[str]]


def parse_redirect_target(value: str | None, *, fallback: str = "/") -> RedirectTarget:
    """Parse a same-app redirect without accepting origins or protocol-relative URLs."""
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if (
        not raw
        or parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or "\\" in parsed.path
    ):
        parsed = urlsplit(fallback)
    query_values = parse_qs(parsed.query, keep_blank_values=True)
    query = {
        key: values[0] if len(values) == 1 else values
        for key, values in query_values.items()
        if key not in HANDOFF_QUERY_KEYS
    }
    return RedirectTarget(path=parsed.path or "/", query=query)


def _get_query_values(query_params: Any, key: str) -> list[str]:
    if hasattr(query_params, "get_all"):
        return [str(value) for value in query_params.get_all(key)]
    value = query_params.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _strip_handoff_query(query_params: Any) -> None:
    remaining: dict[str, str | list[str]] = {}
    for key in query_params:
        if key in HANDOFF_QUERY_KEYS:
            continue
        values = _get_query_values(query_params, key)
        if values:
            remaining[key] = values[0] if len(values) == 1 else values
    query_params.from_dict(remaining)


class StreamlitHandoff:
    """Authenticate a Streamlit session through FloAgent one-time handoff."""

    def __init__(
        self,
        client: HandoffClient,
        *,
        redirect_pages: Mapping[str, Any],
        fallback_redirect: str = "/",
        session_key: str = SESSION_KEY,
    ) -> None:
        if fallback_redirect not in redirect_pages:
            raise ValueError("fallback_redirect must exist in redirect_pages.")
        self.client = client
        self.redirect_pages = dict(redirect_pages)
        self.fallback_redirect = fallback_redirect
        self.session_key = session_key

    def require_session(self) -> FloAgentSession:
        import streamlit as st

        token_values = _get_query_values(st.query_params, "token")
        if token_values:
            self._complete_handoff(st, token_values)

        session = self._load_session(st)
        if session is None:
            message = st.session_state.pop(
                ERROR_KEY,
                "Open this app from FloAgent to sign in.",
            )
            st.error(message)
            st.stop()

        if session.needs_refresh():
            try:
                session = self.client.refresh(session)
            except HandoffError:
                st.session_state.pop(self.session_key, None)
                st.error("Your FloAgent session expired. Reopen the app from FloAgent.")
                st.stop()
            st.session_state[self.session_key] = session.to_dict()

        self._finish_redirect(st)
        return session

    def render_sidebar(self, session: FloAgentSession) -> None:
        import streamlit as st

        profile_name = (
            session.profile.get("display_name")
            or session.profile.get("name")
            or session.profile.get("profile_id")
            or "FloAgent user"
        )
        with st.sidebar:
            st.caption(f"Signed in as {profile_name}")
            if st.button("Log out", key="floagent_logout_button"):
                self.logout()
                st.rerun()

    def logout(self) -> None:
        import streamlit as st

        for key in (
            self.session_key,
            ERROR_KEY,
            REDIRECT_KEY,
            POST_SWITCH_QUERY_KEY,
        ):
            st.session_state.pop(key, None)

    def _complete_handoff(self, st: Any, token_values: list[str]) -> None:
        if len(token_values) != 1:
            self._fail_handoff(st, "The FloAgent sign-in link is invalid.")
            return

        api_base_values = _get_query_values(st.query_params, "api_base_url")
        if len(api_base_values) > 1:
            self._fail_handoff(st, "The FloAgent sign-in link is invalid.")
            return
        redirect_values = _get_query_values(st.query_params, "redirect")
        if len(redirect_values) > 1:
            self._fail_handoff(st, "The FloAgent sign-in link is invalid.")
            return

        try:
            session = self.client.exchange(
                token_values[0],
                api_base_url=api_base_values[0] if api_base_values else None,
            )
        except HandoffError as error:
            self._fail_handoff(st, error.user_message)
            return

        target = parse_redirect_target(
            redirect_values[0] if redirect_values else None,
            fallback=self.fallback_redirect,
        )
        if target.path not in self.redirect_pages:
            target = parse_redirect_target(self.fallback_redirect)
        st.session_state[self.session_key] = session.to_dict()
        st.session_state[REDIRECT_KEY] = {
            "path": target.path,
            "query": target.query,
        }
        _strip_handoff_query(st.query_params)
        st.rerun()

    def _fail_handoff(self, st: Any, message: str) -> None:
        st.session_state.pop(self.session_key, None)
        st.session_state[ERROR_KEY] = message
        _strip_handoff_query(st.query_params)
        st.rerun()

    def _load_session(self, st: Any) -> FloAgentSession | None:
        raw_session = st.session_state.get(self.session_key)
        if not isinstance(raw_session, Mapping):
            return None
        try:
            session = FloAgentSession.from_dict(raw_session)
            self.client.resolve_api_base_url(
                session.service_endpoints.api_base_url
            )
            return session
        except (HandoffError, SessionValidationError, ValueError):
            st.session_state.pop(self.session_key, None)
            return None

    def _finish_redirect(self, st: Any) -> None:
        post_switch_query = st.session_state.pop(POST_SWITCH_QUERY_KEY, None)
        if isinstance(post_switch_query, Mapping):
            st.query_params.from_dict(dict(post_switch_query))

        raw_target = st.session_state.pop(REDIRECT_KEY, None)
        if not isinstance(raw_target, Mapping):
            return
        path = str(raw_target.get("path", self.fallback_redirect))
        page = self.redirect_pages.get(path)
        if page is None:
            page = self.redirect_pages[self.fallback_redirect]
        query = raw_target.get("query")
        if isinstance(query, Mapping) and query:
            st.session_state[POST_SWITCH_QUERY_KEY] = dict(query)
        st.switch_page(page)
