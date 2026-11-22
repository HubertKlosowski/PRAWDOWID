"""Redact obvious identifiers before persistence and external analysis."""

import re
from urllib.parse import urlsplit

URL = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
IDENTIFIER = re.compile(
    r"\b(PESEL|NIP|REGON|(?:numer\s+)?dowodu(?:\s+osobistego)?)\s*[:=]?\s*"
    r"([A-Z]{3}\s*\d{6}|\d(?:[ -]?\d){8,13})(?!\w)", re.IGNORECASE,
)
PESEL = re.compile(r"(?<!\w)\d{11}(?!\w)")
PHONE = re.compile(
    r"(?<!\w)(?:\+48[ .-]?)?(?:\(\d{2}\)[ .-]?\d(?:[ .-]?\d){6}|"
    r"\(\d{3}\)[ .-]?\d(?:[ .-]?\d){5}|\d(?:[ .-]?\d){8})(?!\w)"
)


def url_origin(value):
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return "[URL]"
    if (parts.scheme not in ("http", "https") or not host or len(host) > 253
            or not re.fullmatch(r"[\w.:-]+", host)):
        return "[URL]"
    authority = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{authority}"


def mask_text(text):
    text = URL.sub(lambda match: url_origin(match[0]), text)
    text = EMAIL.sub("[EMAIL]", text)
    text = IDENTIFIER.sub(r"\1 [IDENTYFIKATOR]", text)
    text = PESEL.sub("[PESEL]", text)
    return PHONE.sub("[TELEFON]", text)


def mask_payload(data):
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, list):
        return [mask_payload(item) for item in data]
    if isinstance(data, dict):
        return {key: val if key in {"id", "fetched_at", "published_at", "valid_from", "valid_to"}
                else mask_payload(val) for key, val in data.items()}
    return data
