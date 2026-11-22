"""Session-owned, time-limited verification history and timings."""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic, perf_counter
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from .history_export import answer_snapshot
from .models import RateLimitBucket, SecurityEvent, VerificationRun, VerificationSession
from .privacy import mask_text, url_origin

logger = logging.getLogger(__name__)
COOKIE_NAME = "prawdowid_session"
SESSION_KEY = re.compile(r"[A-Za-z0-9_-]{43}")
_trace = ContextVar("verification_trace", default=None)
_prune_lock = Lock()
_next_prune = 0


def retention_period():
    return timedelta(days=settings.PRAWDOWID_RETENTION_DAYS)


def new_session_key():
    return secrets.token_urlsafe(32)


def valid_session_key(value):
    return isinstance(value, str) and SESSION_KEY.fullmatch(value) is not None


def discord_session_key(guild_id, user_id):
    secret = os.getenv("DISCORD_BOT_TOKEN") or settings.SECRET_KEY
    digest = hmac.digest(secret.encode(), f"prawdowid-session:{guild_id}:{user_id}".encode(), "sha256")
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _key_hash(key, channel):
    return hashlib.sha256(f"{channel}:{key}".encode()).hexdigest()


def active_session_runs(key, channel):
    runs = VerificationRun.objects.filter(channel=channel)
    if not settings.PRAWDOWID_AUDIT_ENABLED or not valid_session_key(key):
        return runs.none()
    return runs.filter(session__key_hash=_key_hash(key, channel), session__expires_at__gt=timezone.now())


def delete_session(key, channel):
    if not valid_session_key(key):
        return
    with transaction.atomic():
        sessions = VerificationSession.objects.select_for_update().filter(key_hash=_key_hash(key, channel))
        for session in sessions:
            session.delete()


def prune_expired(now=None):
    now = timezone.now() if now is None else now
    with transaction.atomic():
        expired = VerificationSession.objects.select_for_update().filter(expires_at__lte=now)
        ids = list(expired.order_by("pk").values_list("pk", flat=True))
        _, counts = VerificationSession.objects.filter(pk__in=ids).delete()
        expired_buckets = list(RateLimitBucket.objects.select_for_update(skip_locked=True)
                               .filter(expires_at__lte=now).values_list("pk", flat=True))
        RateLimitBucket.objects.filter(pk__in=expired_buckets).delete()
        SecurityEvent.objects.filter(created_at__lte=now - retention_period()).delete()
    return {
        "sessions": counts.get(VerificationSession._meta.label, 0),
        "runs": counts.get(VerificationRun._meta.label, 0),
    }


def _prune_if_due():
    global _next_prune
    if monotonic() < _next_prune or not _prune_lock.acquire(blocking=False):
        return
    try:
        if monotonic() >= _next_prune:
            try:
                prune_expired()
            except DatabaseError as exc:
                logger.warning("Audit cleanup unavailable (%s)", type(exc).__name__)
                _next_prune = monotonic() + 60
            else:
                _next_prune = monotonic() + 86400
    finally:
        _prune_lock.release()


@dataclass
class Trace:
    session_id: int
    channel: str
    mode: str
    created_at: datetime
    started: float = field(default_factory=perf_counter)
    request_id: UUID = field(default_factory=uuid4)
    stages: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)


def open_trace(key, channel, mode):
    if not settings.PRAWDOWID_AUDIT_ENABLED or not valid_session_key(key):
        return None
    now = timezone.now()
    with transaction.atomic():
        sessions = VerificationSession.objects.select_for_update()
        lookup = {"key_hash": _key_hash(key, channel)}
        defaults = {"expires_at": now + retention_period()}
        session, _ = sessions.get_or_create(**lookup, defaults=defaults)
        if session.expires_at <= now:
            session.delete()
            session = sessions.create(**lookup, **defaults)
    _prune_if_due()
    return Trace(session.pk, channel, mode, now)


@contextmanager
def traced(trace):
    token = _trace.set(trace)
    try:
        yield
    finally:
        _trace.reset(token)


@contextmanager
def measure(stage):
    trace = _trace.get()
    if trace is None:
        yield
        return
    summary = trace.stages.setdefault(stage, {"ms": 0, "calls": 0, "errors": 0})
    start = perf_counter()
    summary["calls"] += 1
    try:
        yield
    except Exception:
        summary["errors"] += 1
        raise
    finally:
        summary["ms"] += round((perf_counter() - start) * 1000)


def record_security_event(code, *, channel=None):
    trace = _trace.get()
    if trace is None and channel is None:
        return
    try:
        with transaction.atomic():
            SecurityEvent.objects.create(
                code=code, channel=channel or trace.channel,
                request_id=trace.request_id if trace else None, created_at=timezone.now(),
            )
    except DatabaseError as exc:
        logger.warning("Security event unavailable (%s)", type(exc).__name__)


def record_model(role, name, backend, revision=None):
    if trace := _trace.get():
        # A local path can reveal workstation or account names.
        name = Path(name).name if name.startswith(("/", ".", "~")) else name
        metadata = {"id": mask_text(name)[:200], "backend": backend}
        if isinstance(revision, str) and re.fullmatch(r"[a-fA-F0-9]{7,64}", revision):
            metadata["revision"] = revision
        trace.models_used[role] = metadata


def save_run(trace, material, result):
    outcomes = []
    for item in result.get("claims") or [result]:
        outcome = {key: item[key] for key in ("status", "verdict", "reason_code") if key in item}
        if item.get("claim"):
            outcome["claim"] = mask_text(item["claim"])
        outcome["evidence"] = [
            {key: source[key] for key in ("document_id", "chunk_id", "relation") if key in source}
            for source in item.get("evidence", [])
        ]
        outcome["fact_checks"] = [
            {"origin": url_origin(review["source_url"]),
             "id": hashlib.sha256(review["source_url"].encode()).hexdigest()}
            for review in item.get("fact_checks", []) if review.get("cited")
        ]
        outcomes.append(outcome)
    question = ""
    snapshot = None
    if result["status"] != "rejected":
        question = url_origin(material.strip()) if trace.mode == "url" else mask_text(material)
        if trace.channel in ("web", "discord"):
            snapshot = answer_snapshot(result)
    with transaction.atomic():
        # A deletion or expiry during inference invalidates this exact session row.
        session = VerificationSession.objects.select_for_update().filter(
            pk=trace.session_id, expires_at__gt=timezone.now(),
        ).first()
        if session is None:
            return False
        VerificationRun.objects.create(
            request_id=trace.request_id, session=session, channel=trace.channel, mode=trace.mode,
            question=question,
            created_at=trace.created_at, status=result["status"], outcomes=outcomes, answer_snapshot=snapshot,
            elapsed_ms=round((perf_counter() - trace.started) * 1000),
            stages=trace.stages, models_used=trace.models_used,
        )
    return True
