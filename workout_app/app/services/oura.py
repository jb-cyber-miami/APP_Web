"""Oura Ring sync (API v2, OAuth2).

Oura retired personal access tokens in Dec 2025, so each user connects through
OAuth2. Tokens are Fernet-encrypted in the database and refreshed on demand.
Oura refresh tokens are single use, so the new one is always saved.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from flask import current_app

from ..extensions import db
from ..models import HealthSample, Integration, utcnow
from ..security import decrypt, encrypt
from ..timeutil import to_utc_naive

log = logging.getLogger(__name__)

API = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"  # nosec B105 # noqa: S105 - a URL
REVOKE_URL = "https://api.ouraring.com/oauth/revoke"
TIMEOUT = 10


class OuraError(Exception):
    pass


def save_tokens(integration: Integration, token: dict) -> None:
    integration.access_token_enc = encrypt(token["access_token"])
    if token.get("refresh_token"):
        integration.refresh_token_enc = encrypt(token["refresh_token"])
    expires_in = token.get("expires_in")
    if token.get("expires_at"):
        integration.expires_at = datetime.fromtimestamp(int(token["expires_at"]), UTC).replace(tzinfo=None)
    elif expires_in:
        integration.expires_at = utcnow() + timedelta(seconds=int(expires_in))


def _refresh(integration: Integration) -> None:
    refresh_token = decrypt(integration.refresh_token_enc or "")
    if not refresh_token:
        raise OuraError("Oura connection expired. Please reconnect.")
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": current_app.config["OURA_CLIENT_ID"],
            "client_secret": current_app.config["OURA_CLIENT_SECRET"],
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise OuraError("Oura connection expired. Please reconnect.")
    save_tokens(integration, resp.json())
    db.session.commit()


def _access_token(integration: Integration) -> str:
    if integration.expires_at and integration.expires_at - timedelta(minutes=2) <= utcnow():
        _refresh(integration)
    token = decrypt(integration.access_token_enc)
    if not token:
        raise OuraError("Stored Oura credentials are unreadable. Please reconnect.")
    return token


def _get_all(integration: Integration, path: str, params: dict) -> list[dict]:
    items: list[dict] = []
    params = dict(params)
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {_access_token(integration)}"}
        resp = requests.get(f"{API}/{path}", headers=headers, params=params, timeout=TIMEOUT)
        if resp.status_code == 401 and attempt == 0:
            _refresh(integration)
            continue
        break
    while True:
        if resp.status_code != 200:
            raise OuraError(f"Oura returned HTTP {resp.status_code}")
        body = resp.json()
        items.extend(body.get("data") or [])
        next_token = body.get("next_token")
        if not next_token or len(items) > 1000:
            return items
        params["next_token"] = next_token
        resp = requests.get(f"{API}/{path}", headers=headers, params=params, timeout=TIMEOUT)


def _upsert(user_id: int, kind: str, external_id: str, **fields) -> None:
    sample = HealthSample.query.filter_by(
        user_id=user_id, source="oura", kind=kind, external_id=external_id
    ).first()
    if sample is None:
        sample = HealthSample(user_id=user_id, source="oura", kind=kind, external_id=external_id)
        db.session.add(sample)
    for key, value in fields.items():
        setattr(sample, key, value)


def _int(value) -> int:
    try:
        return max(0, min(int(round(float(value or 0))), 20000))
    except (TypeError, ValueError):
        return 0


def sync(integration: Integration, days: int = 7) -> int:
    """Pull the last ``days`` days of daily activity and workouts. Returns rows touched."""
    end = date.today() + timedelta(days=1)  # Oura's end_date is exclusive on some endpoints
    start = end - timedelta(days=days + 1)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    count = 0
    try:
        for item in _get_all(integration, "daily_activity", params):
            _upsert(
                integration.user_id,
                "daily",
                str(item.get("id") or item["day"])[:128],
                day=date.fromisoformat(item["day"]),
                active_kcal=_int(item.get("active_calories")),
                total_kcal=_int(item.get("total_calories")) or None,
            )
            count += 1
        for item in _get_all(integration, "workout", params):
            _upsert(
                integration.user_id,
                "workout",
                str(item["id"])[:128],
                day=date.fromisoformat(item["day"]),
                start_at=to_utc_naive(datetime.fromisoformat(item["start_datetime"])),
                end_at=to_utc_naive(datetime.fromisoformat(item["end_datetime"])),
                active_kcal=_int(item.get("calories")),
                activity=str(item.get("activity") or "")[:60],
            )
            count += 1
    except requests.RequestException as exc:
        integration.last_error = "Could not reach Oura"
        db.session.commit()
        raise OuraError("Could not reach Oura. Try again later.") from exc
    except (KeyError, ValueError) as exc:
        integration.last_error = "Unexpected data from Oura"
        db.session.commit()
        raise OuraError("Oura sent data we couldn't read.") from exc
    except OuraError as exc:
        integration.last_error = str(exc)[:200]
        db.session.commit()
        raise

    integration.last_synced_at = utcnow()
    integration.last_error = None
    db.session.commit()
    return count


def revoke(integration: Integration) -> None:
    token = decrypt(integration.access_token_enc)
    if not token:
        return
    try:
        requests.get(REVOKE_URL, params={"access_token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        log.info("Oura revoke failed; deleting local tokens anyway")
