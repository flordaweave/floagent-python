"""Framework-neutral FloAgent handoff and refresh HTTP client."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ._version import __version__
from .models import FloAgentSession, SessionValidationError


USER_AGENT = f"floagent-sdk-python/{__version__}"


class HandoffError(RuntimeError):
    """Safe, structured failure from a FloAgent authentication operation."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def user_message(self) -> str:
        if self.status in {400, 401, 409}:
            return "The FloAgent sign-in link is invalid, expired, or already used."
        if self.status == 403:
            return "You are not allowed to open this app."
        return "FloAgent sign-in is temporarily unavailable. Please try again."


def normalize_api_base_url(value: str) -> str:
    """Return a canonical HTTP(S) API base URL or raise ValueError."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise ValueError("FloAgent API base URL is invalid.") from error

    hostname = parsed.hostname
    is_local = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in ({"http", "https"} if is_local else {"https"}):
        raise ValueError(
            "FloAgent API base URL must use HTTPS, except for localhost development."
        )
    if not hostname or parsed.username or parsed.password:
        raise ValueError("FloAgent API base URL must contain a valid host.")
    if parsed.query or parsed.fragment:
        raise ValueError("FloAgent API base URL must not contain query parameters or a fragment.")

    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc.lower(), path, "", ""))


class HandoffClient:
    """Exchange one-time handoff tokens and refresh FloAgent sessions."""

    def __init__(
        self,
        default_api_base_url: str,
        *,
        allowed_api_base_urls: Iterable[str] = (),
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        default_url = normalize_api_base_url(default_api_base_url)
        allowed = {
            normalize_api_base_url(url)
            for url in [default_url, *allowed_api_base_urls]
        }
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 60
        ):
            raise ValueError("timeout_seconds must be greater than 0 and at most 60.")
        self.default_api_base_url = default_url
        self.allowed_api_base_urls = frozenset(allowed)
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "HandoffClient":
        raw_allowed = config.get("allowed_api_base_urls", ())
        if isinstance(raw_allowed, str):
            raw_allowed = [raw_allowed]
        if not isinstance(raw_allowed, Iterable):
            raise ValueError("allowed_api_base_urls must be a list of URLs.")
        return cls(
            str(config.get("api_base_url", "")),
            allowed_api_base_urls=[str(value) for value in raw_allowed],
            timeout_seconds=float(config.get("request_timeout_seconds", 10.0)),
        )

    def resolve_api_base_url(self, discovered_url: str | None = None) -> str:
        candidate = normalize_api_base_url(
            discovered_url or self.default_api_base_url
        )
        if candidate not in self.allowed_api_base_urls:
            raise HandoffError("FloAgent API base URL is not allowed.")
        return candidate

    def exchange(
        self,
        token: str,
        *,
        api_base_url: str | None = None,
    ) -> FloAgentSession:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise HandoffError("Handoff token must not be empty.", status=400)
        base_url = self.resolve_api_base_url(api_base_url)
        payload = self._post_json(
            base_url,
            "/auth/login/handoff/exchange",
            {"token": normalized_token},
        )
        return self._parse_session(payload, fallback_api_base_url=base_url)

    def refresh(self, session: FloAgentSession) -> FloAgentSession:
        base_url = self.resolve_api_base_url(
            session.service_endpoints.api_base_url
        )
        payload = self._post_json(
            base_url,
            "/auth/token/refresh",
            {"refresh_token": session.refresh_token},
        )
        return self._parse_session(payload, fallback_api_base_url=base_url)

    def _parse_session(
        self,
        payload: Mapping[str, Any],
        *,
        fallback_api_base_url: str,
    ) -> FloAgentSession:
        try:
            session = FloAgentSession.from_mapping(
                payload,
                fallback_api_base_url=fallback_api_base_url,
            )
            canonical_endpoint = self.resolve_api_base_url(
                session.service_endpoints.api_base_url
            )
        except (HandoffError, SessionValidationError, ValueError) as error:
            raise HandoffError("FloAgent returned an invalid session response.") from error

        if canonical_endpoint == session.service_endpoints.api_base_url:
            return session
        normalized_payload = session.to_dict()
        normalized_payload["service_endpoints"]["api_base_url"] = canonical_endpoint
        return FloAgentSession.from_dict(normalized_payload)

    def _post_json(
        self,
        base_url: str,
        path: str,
        body: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        request = Request(
            f"{base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw_payload = response.read()
        except HTTPError as error:
            code = None
            try:
                error_payload = json.loads(error.read().decode("utf-8"))
                if isinstance(error_payload, Mapping):
                    raw_code = error_payload.get("code") or error_payload.get("error")
                    code = str(raw_code) if raw_code else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise HandoffError(
                "FloAgent authentication request failed.",
                status=error.code,
                code=code,
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise HandoffError("FloAgent authentication service could not be reached.") from error

        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HandoffError("FloAgent returned invalid JSON.") from error
        if not isinstance(payload, Mapping):
            raise HandoffError("FloAgent returned an invalid response.")
        return payload
