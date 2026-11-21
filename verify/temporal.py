"""Date metadata and conservative checks for the period named in a claim."""

import re
from datetime import date, datetime, time, timedelta

from django.utils import timezone

DATE_FIELDS = ("published_at", "valid_from", "valid_to")
ARCHIVE_WARNING = "Część materiału jest archiwalna lub pochodzi sprzed okresu objętego tezą."
POLISH_MONTHS = ("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
                 "sierpnia", "września", "października", "listopada", "grudnia")
PERIOD_MISMATCH = "Znalezione materiały nie potwierdzają stanu w okresie, którego dotyczy zgłoszenie."


def source_date(value):
    if isinstance(value, datetime):
        return local_datetime(value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    return None


def local_datetime(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if not isinstance(value, datetime):
        return None
    zone = timezone.get_default_timezone()
    return timezone.make_aware(value, zone) if timezone.is_naive(value) else value.astimezone(zone)


def claim_period(text, now):
    """Return a local inclusive day range, or an instant for a current-state claim."""
    days = []
    for match in re.finditer(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})\b", text):
        value = match.group()
        if "-" in value:
            day = source_date(value)
        else:
            day, month, year = map(int, re.split(r"[./]", value))
            try:
                day = date(year, month, day)
            except ValueError:
                day = None
        if day:
            days.append(day)
    for day, month, year in re.findall(
        r"\b(\d{1,2})\s+(" + "|".join(POLISH_MONTHS) + r")\s+(\d{4})\b", text.casefold()
    ):
        try:
            days.append(date(int(year), POLISH_MONTHS.index(month) + 1, int(day)))
        except ValueError:
            continue
    if days:
        return _day_range(min(days), max(days), now)
    years = [int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", text)]
    if years:
        return _day_range(date(min(years), 1, 1), date(max(years), 12, 31), now)
    for word, offset in (("wczoraj", -1), ("jutro", 1)):
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            day = now.date() + timedelta(days=offset)
            return _day_range(day, day, now)
    if re.search(r"\b(?:dziś|dzisiaj|teraz|obecnie|aktualnie|obowiązuje|obowiązują|nadal)\b|\b(?:jest|są)\s+(?:ostrzeżenie|alert|utrudnienia|zamknięta)\b", text, re.IGNORECASE):
        return now, now
    return None


def _day_range(first, last, now):
    return datetime.combine(first, time.min, now.tzinfo), datetime.combine(last, time.max, now.tzinfo)


def period_fit(chunk, period):
    """Sort matching metadata first, then unknown dates, then known mismatches."""
    if period is None:
        return 0
    start, end = period
    valid_from = local_datetime(chunk.get("valid_from"))
    valid_to = local_datetime(chunk.get("valid_to"))
    if (valid_to and valid_to < start) or (valid_from and valid_from > end):
        return 2
    published = source_date(chunk.get("published_at"))
    if published and start.date() <= published <= end.date():
        return 0
    if valid_from and valid_to:
        return 0 if start == end or (valid_from <= start and valid_to >= end) else 1
    if start == end and (not valid_from or valid_from.date() != start.date()):
        return 2
    # A retrospective account can still establish a historical fact.
    return 1


def archived(chunk, now, period):
    valid_to = local_datetime(chunk.get("valid_to"))
    published = source_date(chunk.get("published_at"))
    return bool(
        (valid_to and valid_to < now)
        or (period and published and (
            published < period[0].date()
            or (period[1].date() < now.date() and published <= period[1].date())
        ))
    )
