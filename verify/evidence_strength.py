"""Deterministic explanation of the basis for each verification result."""

from collections import Counter
from urllib.parse import urlsplit

from .temporal import archived, period_fit

LEVELS = {
    "insufficient": "Niewystarczająca",
    "limited": "Ograniczona",
    "moderate": "Umiarkowana",
    "strong": "Silna",
}
SOURCE_TYPES = {
    "official": "urzędowe",
    "primary": "pierwotne",
    "secondary": "wtórne",
    "unknown": "rodzaj nieustalony",
}


def _source_host(item):
    if domain := item.get("source_domain"):
        return domain
    host = (urlsplit(item["url"]).hostname or "").lower().rstrip(".").removeprefix("www.")
    return "gov.pl" if host == "gov.pl" or host.endswith(".gov.pl") else host


def _source_type(item):
    declared = item.get("source_type")
    if declared in SOURCE_TYPES and declared != "unknown":
        return declared
    host = _source_host(item)
    return "official" if host == "gov.pl" or host.endswith(".gov.pl") else "unknown"


def _source_groups(items):
    """Count distinct known publishers or domains, discounting duplicate citations."""
    groups = []
    for item in items:
        keys = {("document", item["document_id"]), ("quote", " ".join(item["quote"].casefold().split()))}
        if publisher := (item.get("publisher") or "").strip().casefold():
            keys.add(("publisher", publisher))
        if host := _source_host(item):
            keys.add(("domain", host))
        remaining = []
        for group in groups:
            if keys & group:
                keys.update(group)
            else:
                remaining.append(group)
        groups = [*remaining, keys]
    return groups


def evidence_strength(result, *, now, period):
    direct = [item for item in result["evidence"] if item["relation"] != "context"]
    documents = len({item["document_id"] for item in direct})
    direction = "contradicts" if result["verdict"] == "false" else "supports"
    same_direction = [item for item in direct if item["relation"] == direction]
    confirmations = len(_source_groups(same_direction))
    types = Counter(_source_type(item) for item in {item["document_id"]: item for item in direct}.values())
    time_matches = period is not None and bool(direct) and all(period_fit(item, period) == 0 for item in direct)
    old = any(archived(item, now, period) for item in direct)
    temporal = "matched" if time_matches else "archived" if old else "unconfirmed" if period else "unspecified"

    level = "insufficient"
    if result["status"] == "resolved":
        level = "limited" if temporal in ("archived", "unconfirmed") else "moderate"
        if result["verdict"] != "misleading" and confirmations >= 2 and time_matches and (
            types["official"] or types["primary"]
        ):
            level = "strong"

    factors = [
        f"Cytaty bezpośrednio odnoszące się do tezy lub zmieniające jej sens: {len(direct)}. Dokumenty: {documents}.",
        f"Odrębni wydawcy lub domeny cytatów zgodnych z oceną: {confirmations}."
        if result["status"] == "resolved" else "Cytaty nie pozwalają wydać werdyktu.",
    ]
    if direct:
        kinds = ", ".join(f"{SOURCE_TYPES[key]} ({count})" for key, count in sorted(types.items()))
        factors.append(f"Rodzaje źródeł: {kinds}.")
        factors.append({
            "matched": "Daty lub okresy ważności dowodów pasują do okresu tezy.",
            "archived": "Część dowodów pochodzi z materiałów archiwalnych lub wcześniejszego okresu.",
            "unconfirmed": "Metadane części dowodów nie potwierdzają zgodności z okresem tezy.",
            "unspecified": "W tezie nie ustalono konkretnego okresu.",
        }[temporal])
    return {
        "level": level, "label": LEVELS[level], "factors": factors,
        "direct_citations": len(direct), "documents": documents,
        "source_groups_in_agreement": confirmations if result["status"] == "resolved" else None,
        "source_types": dict(types), "temporal_fit": temporal,
    }
