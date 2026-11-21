"""Verification workflow shared by the web form and the demo command."""

import logging
import os

from django.utils import timezone

from .analysis import assess, select_relevant, unresolved
from .fetch import SourceUnavailable, fetch
from .retrieval import search as search_corpus
from .temporal import ARCHIVE_WARNING, PERIOD_MISMATCH, archived, claim_period, period_fit

MAX_MATERIAL_LENGTH = 12000
logger = logging.getLogger(__name__)


class InvalidMaterial(ValueError):
    """The submission failed input validation."""


def verify(material, *, mode="text", complete=None, search=None):
    """Verify a submission using optional completion and corpus search callables.

    complete(system_prompt, data) returns decoded model output.
    search(text, limit) returns ranked fragments with the metadata of search_corpus.
    """
    if mode not in ("text", "url"):
        raise InvalidMaterial("Wybierz tekst lub adres URL.")
    material = material.strip()
    if not material:
        raise InvalidMaterial("Wpisz treść lub adres strony.")
    if len(material) > MAX_MATERIAL_LENGTH:
        raise InvalidMaterial("Materiał może mieć najwyżej 12 000 znaków.")

    if mode == "url":
        try:
            _, _, material = fetch(material)
        except SourceUnavailable as exc:
            return unresolved("abstained", str(exc))
        except ValueError as exc:
            raise InvalidMaterial(str(exc)) from exc
        if len(material) > MAX_MATERIAL_LENGTH:
            return unresolved("abstained", "Artykuł przekracza limit. Wklej fragment do sprawdzenia.")

    now = timezone.localtime()
    period = claim_period(material, now)
    search = search_corpus if search is None else search
    try:
        limit = max(1, min(30, int(os.getenv("RAG_TOP_K", "5"))))
        candidates = search(material, min(40, limit * 3))
        ranked = sorted(((period_fit(chunk, period), chunk) for chunk in candidates), key=lambda item: item[0])
        eligible = [chunk for fit, chunk in ranked if fit < 2]
        if candidates and not eligible:
            result = unresolved("abstained", PERIOD_MISMATCH)
            if any(archived(chunk, now, period) for chunk in candidates):
                result["limitations"] = [ARCHIVE_WARNING]
            return result
        selected = select_relevant(material, eligible, limit, complete=complete, period=period)
    except Exception as exc:
        logger.warning("Verification retrieval unavailable (%s)", type(exc).__name__)
        return unresolved(
            "technical_error",
            "Wyszukiwanie źródeł jest chwilowo niedostępne. Spróbuj ponownie później.",
        )

    # Assess the nearest matches when selection may have missed relevant evidence.
    result = assess(material, selected or eligible[:limit], complete=complete, period=period)
    if any(archived(item, now, period) for item in result["evidence"]):
        result["limitations"] = [ARCHIVE_WARNING]
    return result
