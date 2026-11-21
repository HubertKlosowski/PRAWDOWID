"""Bounded access to Google Fact Check Tools claim reviews."""

import json
import re
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from .providers import NoRedirect

ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
PAGE_SIZE = 10
MAX_REVIEWS_PER_QUERY = 20
MAX_RESPONSE_BYTES = 256 * 1024
TIMEOUT = 4
validate_url = URLValidator(schemes=["http", "https"])
POLISH_QUESTION = re.compile(r"czy ([^.!?]+)\?", re.IGNORECASE)
RECENT_EVENT_PHRASE = re.compile(r"\b(?:ostatnio|niedawno) odby(?:wa)?(?:ł|ła|ło|li|ły) się\b", re.IGNORECASE)


class FactCheckUnavailable(RuntimeError):
    """A lookup failed. The message contains only safe diagnostics."""


@dataclass(frozen=True)
class FactCheck:
    claim: str
    claimant: str
    claim_date: str
    rating: str
    publisher: str
    publisher_site: str
    review_title: str
    review_date: str
    language_code: str
    source_url: str

    @property
    def claim_key(self):
        return self.claim, self.claimant, self.claim_date

    @property
    def review_key(self):
        return (self.claim_key, self.source_url.split("#", 1)[0],
                self.rating, self.publisher, self.review_date)


def _field(record, name, limit=500):
    value = record.get(name)
    if isinstance(value, str):
        value = value.strip()
        if len(value) <= limit:
            return value
    return ""


def _records(value):
    if isinstance(value, list):
        return (record for record in value if isinstance(record, dict))
    return ()


def _source_url(review):
    url = _field(review, "url", 2048)
    if re.search(r"[\s\\\x00-\x1f\x7f]", url):
        return ""
    try:
        validate_url(url)
        parts = urlsplit(url)
        if not parts.netloc or parts.username is not None or parts.port == 0:
            return ""
    except (ValidationError, ValueError):
        return ""
    return url


def _parse(payload, api_key):
    if not isinstance(payload, dict) or "error" in payload or not isinstance(
        claims := payload.get("claims", []), list
    ):
        raise FactCheckUnavailable("niepoprawna struktura odpowiedzi")

    reviews = {}
    for claim in _records(claims[:PAGE_SIZE]):
        text = _field(claim, "text", 1200)
        if not text:
            continue
        for review in _records(claim.get("claimReview")):
            url = _source_url(review)
            rating = _field(review, "textualRating", 250)
            if not url or not rating:
                continue
            publisher = review.get("publisher")
            publisher = publisher if isinstance(publisher, dict) else {}
            fact_check = FactCheck(
                claim=text,
                claimant=_field(claim, "claimant", 250),
                claim_date=_field(claim, "claimDate", 64),
                rating=rating,
                publisher=_field(publisher, "name", 250),
                publisher_site=_field(publisher, "site", 250),
                review_title=_field(review, "title"),
                review_date=_field(review, "reviewDate", 64),
                language_code=_field(review, "languageCode", 32),
                source_url=url,
            )
            # A response must not carry the credential into displayed links or model data.
            if any(api_key in value for value in vars(fact_check).values()):
                raise FactCheckUnavailable("odpowiedź zawiera dane uwierzytelniające")
            reviews.setdefault(fact_check.review_key, fact_check)
            if len(reviews) == MAX_REVIEWS_PER_QUERY:
                return list(reviews.values())
    return list(reviews.values())


def search_query(text, language_code):
    """Trim common Polish question phrasing for search while keeping the original claim available."""
    if language_code != "pl":
        return text
    match = POLISH_QUESTION.fullmatch(text)
    if not match:
        return text
    keywords = " ".join(RECENT_EVENT_PHRASE.sub(" ", match[1]).split())
    return keywords if " " in keywords else text


def search(query, *, api_key, language_code="pl"):
    params = {"key": api_key, "query": query, "pageSize": PAGE_SIZE}
    if language_code:
        params["languageCode"] = language_code
    try:
        request = Request(f"{ENDPOINT}?{urlencode(params)}", headers={"Accept": "application/json"})
        with build_opener(NoRedirect()).open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise FactCheckUnavailable("przekroczono limit rozmiaru odpowiedzi")
        return _parse(json.loads(body), api_key)
    except HTTPError as exc:
        exc.close()
        raise FactCheckUnavailable(f"HTTP {exc.code}") from None
    except (OSError, HTTPException):
        raise FactCheckUnavailable("błąd połączenia lub przekroczony czas") from None
    except (ValueError, RecursionError):
        raise FactCheckUnavailable("niepoprawny JSON") from None
