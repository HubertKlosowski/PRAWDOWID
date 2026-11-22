"""Verification workflow shared by the web form and the demo command."""

import logging
import os
import re
import sys
from json import JSONDecodeError
from time import perf_counter

from django.db import DatabaseError
from django.utils import timezone

from . import audit, google_fact_check
from .analysis import assess, fact_check_queries, select_fact_checks, select_relevant, unresolved
from .claims import extract_claims
from .evidence_strength import evidence_strength
from .fetch import SourceUnavailable, fetch
from .privacy import mask_text
from .retrieval import search as search_corpus
from .temporal import ARCHIVE_WARNING, PERIOD_MISMATCH, archived, claim_period, period_fit

MAX_MATERIAL_LENGTH = 12000
logger = logging.getLogger(__name__)


class InvalidMaterial(ValueError):
    """The submission failed input validation."""

    def __init__(self, message, *, reason_code="invalid_material"):
        super().__init__(message)
        self.reason_code = reason_code


def material_mode(material):
    return "url" if re.fullmatch(r"https?://\S*", material.strip(), flags=re.IGNORECASE) else "text"


def verify(material, *, mode="text", complete=None, search=None, session_key=None, channel="cli"):
    """Verify a submission using optional completion and corpus search callables.

    complete(system_prompt, data) returns decoded model output.
    search(text, limit) returns ranked fragments with the metadata of search_corpus.
    """
    started = perf_counter()
    trace = None
    audit_unavailable = False
    try:
        trace = audit.open_trace(session_key, channel, mode if mode in ("text", "url") else "text")
        if trace is not None:
            trace.started = started
    except DatabaseError as exc:
        logger.warning("Verification history unavailable (%s)", type(exc).__name__)
        audit_unavailable = True

    try:
        with audit.traced(trace):
            try:
                result = _validate_and_verify(material, mode, complete, search)
            except InvalidMaterial as exc:
                _persist_trace(trace, material, {"status": "rejected", "reason_code": exc.reason_code})
                raise
            except Exception as exc:
                logger.warning("Verification failed (%s)", type(exc).__name__)
                result = unresolved("technical_error", reason_code="unexpected_failure")

        if trace is not None:
            result["request_id"] = str(trace.request_id)
            audit_unavailable = not _persist_trace(trace, material, result)
        if audit_unavailable:
            result["audit_unavailable"] = True
        return result
    finally:
        _release_cuda_cache()


def _persist_trace(trace, material, result):
    if trace is None:
        return False
    try:
        return audit.save_run(trace, material, result)
    except DatabaseError as exc:
        logger.warning("Verification history unavailable (%s)", type(exc).__name__)
        return False


def _validate_and_verify(material, mode, complete, search):
    if mode not in ("text", "url"):
        raise InvalidMaterial("Wybierz tekst lub adres URL.")
    if not isinstance(material, str):
        raise InvalidMaterial("Wpisz treść lub adres strony.")
    material = material.strip()
    if not material:
        raise InvalidMaterial("Wpisz treść lub adres strony.")
    if len(material) > MAX_MATERIAL_LENGTH:
        raise InvalidMaterial("Materiał może mieć najwyżej 12 000 znaków.")

    return _verify_material(material, mode, complete, search)


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
            with audit.measure("fetch"):
                _, _, material = fetch(material)
        except SourceUnavailable as exc:
            return unresolved("material_unavailable", str(exc), reason_code="article_unreadable")
        except ValueError as exc:
            raise InvalidMaterial(str(exc), reason_code="invalid_url") from exc
        if len(material) > MAX_MATERIAL_LENGTH:
            return unresolved("material_unavailable", "Artykuł przekracza limit. Wklej fragment do sprawdzenia.",
                              reason_code="article_too_long")

    if os.getenv("PRAWDOWID_LLM_URL"):
        # Claim extraction checks exact excerpts against the same material the model sees.
        material = mask_text(material)
    try:
        claims, truncated = extract_claims(material, complete=complete)
    except Exception as exc:
        logger.warning("Claim extraction unavailable (%s)", type(exc).__name__)
        return unresolved("technical_error", "Nie udało się wydzielić twierdzeń ze zgłoszenia.",
                          reason_code="claim_extraction_failed")

    now = timezone.localtime()
    if not claims:
        result = assess(material, [], complete=complete)
        if result["status"] == "abstained":
            result = unresolved("abstained", reason_code="claim_not_extracted")
            result["evidence_strength"] = evidence_strength(result, now=now, period=None)
        return result

    split = len(claims) > 1 or claims[0] != (" ".join(material.split()), None)
    results = []
    for text, context in claims:
        query = material if not split else text
        if context is not None:
            query = f"{text}\nKontekst zgłoszenia: {context}"
        result = _verify_claim(query, mode, complete, search, now)
        results.append({**result, "claim": text, "claim_context": context})

    if len(results) == 1 and not truncated:
        return results[0]
    return {"status": "multiple_claims", "verdict": None, "claims": results, "claims_truncated": truncated}


def _verify_claim(material, mode, complete, search, now):
    period = claim_period(material, now)
    fact_checks = []
    google_unavailable = False
    if api_key := os.getenv("GOOGLE_FACTCHECK_API_KEY", "").strip():
        _, fact_checks, google_unavailable = _lookup_fact_checks(material, mode, api_key, complete, period)

    result = _verify_evidence(material, fact_checks, complete, search, now, period)
    if result["status"] in ("resolved", "abstained"):
        result["evidence_strength"] = evidence_strength(result, now=now, period=period)
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
            with audit.measure("google"):
                matches = google_fact_check.search(search_query, api_key=api_key, language_code=language)
            for review in matches:
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


def _verify_evidence(material, fact_checks, complete, search, now, period):
    search = search_corpus if search is None else search
    try:
        limit = max(1, min(30, int(os.getenv("RAG_TOP_K", "5"))))
        with audit.measure("retrieval"):
            candidates = search(material, min(40, limit * 3))
        ranked = sorted(((period_fit(chunk, period), chunk) for chunk in candidates), key=lambda item: item[0])
        eligible = [chunk for fit, chunk in ranked if fit < 2]
        try:
            selected = select_relevant(material, eligible, limit, complete=complete, period=period)
        except JSONDecodeError:
            logger.warning("Model returned invalid JSON while ranking candidates")
            selected = []
    except Exception as exc:
        logger.warning("Verification retrieval unavailable (%s)", type(exc).__name__)
        return unresolved(
            "technical_error",
            "Wyszukiwanie źródeł jest chwilowo niedostępne. Spróbuj ponownie później.",
            reason_code="retrieval_failed",
        )

    if candidates and not eligible and not fact_checks:
        result = unresolved("abstained", PERIOD_MISMATCH, reason_code="period_mismatch")
    else:
        # Assess the nearest matches when selection may have missed relevant evidence.
        result = assess(material, selected or eligible[:limit], fact_checks=fact_checks,
                        checkable=True, complete=complete, period=period)
    if candidates and not eligible and result["status"] == "abstained":
        result["explanation"] = PERIOD_MISMATCH
        result["reason_code"] = "period_mismatch"
        if any(archived(chunk, now, period) for chunk in candidates):
            result["limitations"] = [ARCHIVE_WARNING]
    if any(archived(item, now, period) for item in result["evidence"]):
        result["limitations"] = [ARCHIVE_WARNING]
    return result
