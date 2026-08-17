"""Typed session models returned by the FloAgent authentication API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class SessionValidationError(ValueError):
    """Raised when FloAgent returns an invalid session payload."""


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SessionValidationError(f"Session field {key!r} is missing or invalid.")
    return value.strip()


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SessionValidationError(
            "Session access_token_expires_at is invalid."
        ) from error
    if parsed.tzinfo is None:
        raise SessionValidationError(
            "Session access_token_expires_at must include a timezone."
        )
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ServiceEndpoints:
    """Canonical FloAgent service endpoints for an authenticated session."""

    api_base_url: str
    web_base_url: str | None = None


@dataclass(frozen=True)
class FloAgentSession:
    """Authenticated FloAgent browser session held by an external app."""

    access_token: str
    refresh_token: str
    access_token_expires_at: datetime
    profile: dict[str, Any]
    service_endpoints: ServiceEndpoints
    pending_link: bool = False

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        fallback_api_base_url: str,
    ) -> "FloAgentSession":
        profile = payload.get("profile")
        if not isinstance(profile, Mapping):
            raise SessionValidationError("Session profile is missing or invalid.")

        raw_endpoints = payload.get("service_endpoints")
        if raw_endpoints is None:
            endpoints = ServiceEndpoints(api_base_url=fallback_api_base_url)
        elif isinstance(raw_endpoints, Mapping):
            api_base_url = _required_string(raw_endpoints, "api_base_url")
            raw_web_base_url = raw_endpoints.get("web_base_url")
            if raw_web_base_url is not None and not isinstance(raw_web_base_url, str):
                raise SessionValidationError(
                    "Session service_endpoints.web_base_url is invalid."
                )
            endpoints = ServiceEndpoints(
                api_base_url=api_base_url,
                web_base_url=(raw_web_base_url.strip() or None)
                if isinstance(raw_web_base_url, str)
                else None,
            )
        else:
            raise SessionValidationError("Session service_endpoints is invalid.")

        return cls(
            access_token=_required_string(payload, "access_token"),
            refresh_token=_required_string(payload, "refresh_token"),
            access_token_expires_at=_parse_datetime(
                _required_string(payload, "access_token_expires_at")
            ),
            profile={str(key): value for key, value in profile.items()},
            service_endpoints=endpoints,
            pending_link=bool(payload.get("pending_link", False)),
        )

    def needs_refresh(
        self,
        *,
        now: datetime | None = None,
        leeway_seconds: int = 30,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return self.access_token_expires_at <= current + timedelta(
            seconds=max(0, leeway_seconds)
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["access_token_expires_at"] = self.access_token_expires_at.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FloAgentSession":
        endpoints = payload.get("service_endpoints")
        normalized = {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "access_token_expires_at": payload.get("access_token_expires_at"),
            "profile": payload.get("profile"),
            "pending_link": payload.get("pending_link", False),
            "service_endpoints": endpoints,
        }
        fallback = (
            str(endpoints.get("api_base_url", ""))
            if isinstance(endpoints, Mapping)
            else ""
        )
        return cls.from_mapping(normalized, fallback_api_base_url=fallback)
