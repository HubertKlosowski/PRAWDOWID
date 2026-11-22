"""Admission control shared by the public verification channels."""

import hashlib
import hmac
from datetime import timedelta
from ipaddress import ip_address
from math import ceil

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .audit import record_security_event
from .models import RateLimitBucket


def enforce_limit(channel, session_key, *, client_ip=None):
    """Reserve one request or return the seconds until another is allowed."""
    if not settings.PRAWDOWID_RATE_LIMIT_ENABLED:
        return None
    keys = []
    session = hashlib.sha256(f"{channel}:{session_key}".encode()).hexdigest()
    if client_ip:
        try:
            address = ip_address(client_ip)
        except ValueError:
            pass
        else:
            # Treat IPv4 and its IPv6-mapped representation as the same peer.
            address = getattr(address, "ipv4_mapped", None) or address
            keys.append(hmac.digest(settings.SECRET_KEY.encode(), f"web-ip:{address}".encode(), "sha256").hex())

    # Check the IP first so a blocked peer cannot create unlimited session buckets.
    keys.append(session)
    period = settings.PRAWDOWID_RATE_WINDOW_SECONDS
    limit = settings.PRAWDOWID_REQUEST_LIMIT
    with transaction.atomic():
        reservations = []
        for key in keys:
            row, _ = RateLimitBucket.objects.select_for_update().get_or_create(
                key=key, defaults={"expires_at": timezone.now()},
            )
            now = timezone.now()
            current = now.timestamp()
            stamps = [stamp for stamp in row.attempts if stamp > current - period]
            if len(stamps) >= limit:
                if not row.last_blocked_at or row.last_blocked_at <= now - timedelta(seconds=period):
                    record_security_event("rate_limit", channel=channel)
                    row.last_blocked_at = now
                    row.save(update_fields=["last_blocked_at"])
                return max(1, ceil(stamps[0] + period - current))
            reservations.append((row, stamps))
        for row, stamps in reservations:
            row.attempts = [*stamps, current]
            row.expires_at = now + timedelta(seconds=period)
            row.save(update_fields=["attempts", "expires_at"])
    return None


def limit_message(seconds):
    return f"Limit sprawdzeń został osiągnięty. Spróbuj ponownie za {seconds} s."
