"""Verification workflow shared by the web form and the demo command."""

import logging
import os
import re
import sys

from django.utils import timezone

from . import google_fact_check
from .analysis import assess, fact_check_queries, select_fact_checks, select_relevant, unresolved
from .fetch import SourceUnavailable, fetch
from .retrieval import search as search_corpus
from .temporal import ARCHIVE_WARNING, PERIOD_MISMATCH, archived, claim_period, period_fit

MAX_MATERIAL_LENGTH = 12000
logger = logging.getLogger(__name__)


class InvalidMaterial(ValueError):
    """The submission failed input validation."""


def material_mode(material):
    return "url" if re.fullmatch(r"https?://\S*", material.strip(), flags=re.IGNORECASE) else "text"


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

    try:
        return _verify_material(material, mode, complete, search)
    finally:
        _release_cuda_cache()


def _release_cuda_cache():
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_initialized():
        try:
            torch.cuda.empty_cache()
        except RuntimeError as exc:
            logger.warning("CUDA cache cleanup failed (%s)", type(exc).__name__)


def _verify_material(material, mode, complete, search):
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
    fact_checks = []
    google_unavailable = False
    checkable = False
    if api_key := os.getenv("GOOGLE_FACTCHECK_API_KEY", "").strip():
        status, fact_checks, google_unavailable = _lookup_fact_checks(material, mode, api_key, complete, period)
        if status in ("needs_clarification", "out_of_scope"):
            return unresolved(status)
        checkable = status == "checkable"

    result = _verify_evidence(material, fact_checks, checkable, complete, search, now, period)
    if google_unavailable:
        result["google_fact_check_unavailable"] = True
    return result


def _lookup_fact_checks(material, mode, api_key, complete, period):
    try:
        status, queries = fact_check_queries(material, mode=mode, complete=complete)
        if not queries:
            return status, [], False
    except Exception as exc:
        logger.warning("Google Fact Check query extraction unavailable (%s)", type(exc).__name__)
        return None, [], True

    reviews = {}
    unavailable = False
    for query, language in queries:
        search_query = google_fact_check.search_query(query, language)
        try:
            for review in google_fact_check.search(search_query, api_key=api_key, language_code=language):
                reviews.setdefault(review.review_key, review)
        except google_fact_check.FactCheckUnavailable as exc:
            logger.warning("Google Fact Check lookup unavailable (%s)", exc)
            unavailable = True
            break
    if not reviews:
        return status, [], unavailable

    try:
        return status, select_fact_checks(material, queries, reviews.values(), complete=complete, period=period), unavailable
    except Exception as exc:
        logger.warning("Google Fact Check relevance unavailable (%s)", type(exc).__name__)
        return status, [], True


def _verify_evidence(material, fact_checks, checkable, complete, search, now, period):
    search = search_corpus if search is None else search
    try:
        limit = max(1, min(30, int(os.getenv("RAG_TOP_K", "5"))))
        candidates = search(material, min(40, limit * 3))
        ranked = sorted(((period_fit(chunk, period), chunk) for chunk in candidates), key=lambda item: item[0])
        eligible = [chunk for fit, chunk in ranked if fit < 2]
        selected = select_relevant(material, eligible, limit, complete=complete, period=period)
    except Exception as exc:
        logger.warning("Verification retrieval unavailable (%s)", type(exc).__name__)
        return unresolved(
            "technical_error",
            "Wyszukiwanie źródeł jest chwilowo niedostępne. Spróbuj ponownie później.",
        )

    if candidates and not eligible and not fact_checks:
        result = unresolved("abstained", PERIOD_MISMATCH)
    else:
        # Assess the nearest matches when selection may have missed relevant evidence.
        result = assess(material, selected or eligible[:limit], fact_checks=fact_checks,
                        checkable=checkable, complete=complete, period=period)
    if candidates and not eligible and result["status"] == "abstained":
        result["explanation"] = PERIOD_MISMATCH
        if any(archived(chunk, now, period) for chunk in candidates):
            result["limitations"] = [ARCHIVE_WARNING]
    if any(archived(item, now, period) for item in result["evidence"]):
        result["limitations"] = [ARCHIVE_WARNING]
    return result
