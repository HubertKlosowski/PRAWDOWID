"""Privacy-filtered answer snapshots and the JSON format shared by WWW and Discord."""

from contextlib import closing
from tempfile import SpooledTemporaryFile
from urllib.parse import unquote, urlsplit

from django.core.serializers.json import DjangoJSONEncoder

from .privacy import mask_payload, mask_text, url_origin

EXPORT_ANSWER_FILENAME = "prawdowid-odpowiedz.json"
EXPORT_HISTORY_FILENAME = "prawdowid-historia.json"
NO_ANSWER = "Brak zapisanej odpowiedzi w tej sesji."
NO_HISTORY = "Brak zapisanych sprawdzeń w tej sesji."
EXPORT_UNAVAILABLE = "Nie udało się pobrać danych. Spróbuj ponownie później."


class ExportTooLarge(ValueError):
    """The JSON file exceeds the delivery channel's attachment limit."""


def _fields(record, *, values=(), text=()):
    fields = {key: record[key] for key in values if key in record}
    fields.update({key: mask_payload(record[key]) for key in text if key in record})
    return fields


def _public_link(url):
    # The declared evidence URL can link to a public article. Drop credentials,
    # query and fragment, and fall back to the origin if the path needs redaction.
    origin = url_origin(url)
    if origin == "[URL]":
        return None
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None:
        origin = f"{origin}:{port}"
    decoded_path = unquote(parts.path)
    return origin + parts.path if decoded_path == mask_text(decoded_path) else origin


def answer_snapshot(result):
    outcomes = []
    for item in result.get("claims") or [result]:
        outcome = _fields(item, values=("status", "verdict", "reason_code", "class_scores", "score_type",
                                        "google_fact_check_unavailable"),
                          text=("claim", "claim_context", "explanation", "limitations", "evidence_strength"))
        outcome["evidence"] = [
            {**_fields(source, values=("id", "document_id", "chunk_id", "position", "relation", "published_at",
                                      "retrieved_at", "valid_from", "valid_to"),
                       text=("title", "publisher", "source_kind", "source_type", "quote")),
             "url": _public_link(source["url"]) if source.get("url") else None}
            for source in item.get("evidence", [])
        ]
        outcome["fact_checks"] = [
            {**_fields(review, values=("id", "cited"),
                       text=("claim", "claim_date", "rating", "publisher", "publisher_site", "review_title",
                             "review_date", "language_code")),
             "source_url": _public_link(review["source_url"])}
            for review in item.get("fact_checks", [])
        ]
        outcomes.append(outcome)
    return {"claims_truncated": result.get("claims_truncated", False), "outcomes": outcomes}


def _export_run(run, latest_only):
    record = {
        "request_id": run.request_id,
        "created_at": run.created_at,
        "channel": run.channel,
        "mode": run.mode,
        "question": run.question,
        "status": run.status,
        "detail_level": "summary",
        "outcomes": run.outcomes,
    }
    if latest_only and run.answer_snapshot is not None:
        record.update(run.answer_snapshot, detail_level="full")
    return record


def build_export(runs, *, latest_only, max_bytes=None):
    """Return an open JSON file, or None if the requested export is empty."""
    runs = runs.defer("stages", "models_used").order_by("-created_at", "-request_id")
    if latest_only:
        runs = runs.exclude(status="rejected")[:1]
    else:
        runs = runs.defer("answer_snapshot")

    with closing(runs.iterator()) as records:
        first = next(records, None)
        if first is None:
            return None

        encoder = DjangoJSONEncoder(ensure_ascii=False, separators=(",", ":"))
        file = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        try:
            export_type = "answer" if latest_only else "history"
            file.write(f'{{"schema_version":1,"export_type":"{export_type}","runs":['.encode())
            file.write(encoder.encode(_export_run(first, latest_only)).encode())
            for run in records:
                if max_bytes is not None and file.tell() + 2 > max_bytes:
                    raise ExportTooLarge
                file.write(b",")
                file.write(encoder.encode(_export_run(run, latest_only)).encode())
            file.write(b"]}\n")
            if max_bytes is not None and file.tell() > max_bytes:
                raise ExportTooLarge
            file.seek(0)
        except BaseException:
            file.close()
            raise
        return file
