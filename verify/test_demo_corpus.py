import csv
import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .analysis import EVIDENCE_PROMPT, RELEVANCE_PROMPT, SYSTEM_PROMPT
from .claims import CLAIMS_PROMPT
from .corpus import load_demo_corpus
from .models import Chunk, Document
from .retrieval import search
from .test_analysis import REPLY
from .verification import verify

RSO_URL = "https://www.gov.pl/web/mswia/regionalny-system-ostrzegania"


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="", PRAWDOWID_LLM_URL="")
class DemoCorpusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.import_summary = load_demo_corpus()

    def test_bundle_restores_auditable_corpus_and_publication_dates(self):
        self.assertEqual(self.import_summary["created_documents"], 435)
        self.assertEqual(self.import_summary["indexed_chunks"], 543)
        output = StringIO()
        call_command("audit_corpus", "--check", stdout=output)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["documents"], 435)
        self.assertEqual(summary["distinct_source_domains"], 10)
        self.assertEqual(summary["distinct_source_types"], 8)
        self.assertEqual(summary["source_roles"], ["official", "primary", "secondary"])
        self.assertEqual(summary["known_validity_periods"], 410)
        self.assertEqual(summary["known_publication_dates"], 9)

        mswia = Document.objects.get(url=RSO_URL)
        self.assertEqual(mswia.publisher, "MSWiA")
        self.assertIsNone(mswia.published_at)
        measure = Document.objects.filter(source_kind="pomiar").first()
        self.assertIsNotNone(measure.valid_from)
        self.assertIsNotNone(measure.valid_to)
        self.assertIsNone(measure.published_at)

    def test_loading_again_restores_only_missing_vectors_for_the_same_content(self):
        document = Document.objects.get(url=RSO_URL)
        chunk = document.chunks.order_by("position").first()
        Chunk.objects.filter(pk=chunk.pk).update(embedding=None)
        other = Document.objects.exclude(pk=document.pk).first()
        other.content_hash = "f" * 64
        other.title = "Nowsza wersja testowa"
        other.valid_from = other.valid_to = other.published_at = None
        other.save()
        summary = load_demo_corpus()
        chunk.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(summary["created_documents"], 0)
        self.assertEqual(summary["indexed_chunks"], 1)
        self.assertEqual(summary["different_versions"], 1)
        self.assertIsNotNone(chunk.embedding)
        self.assertEqual(other.title, "Nowsza wersja testowa")
        self.assertIsNone(other.valid_to)
        self.assertEqual(Chunk.objects.count(), 543)

    def test_evidence_references_same_document_as_audit_csv(self):
        material = "Regionalny System Ostrzegania jest darmowy."
        row = Chunk.objects.get(document__url=RSO_URL, text__contains="darmowa, powszechna usługa")
        with patch("verify.retrieval.embed", return_value=[row.embedding]):
            indexed = search(material, 1)[0]
        self.assertEqual(indexed["id"], str(row.pk))

        def complete(prompt, data):
            if prompt == CLAIMS_PROMPT:
                return {"claims": [{"text": material, "context": None}], "truncated": False}
            if prompt == RELEVANCE_PROMPT:
                return {"chunk_ids": [indexed["id"]]}
            if prompt == SYSTEM_PROMPT:
                quote_id = next(key for key, text in data["fragments"][0]["quotes"].items()
                                if "darmowa, powszechna usługa" in text)
                return {**REPLY, "quote_ids": [quote_id]}
            self.assertEqual(prompt, EVIDENCE_PROMPT)
            quote_id = data["citations"][0]["id"]
            return {"relation": "supports", "citations": {quote_id: "supports"}}

        result = verify(material, search=lambda *_: [indexed], complete=complete)
        self.assertEqual(result["status"], "resolved")
        evidence = result["evidence"][0]
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "audit.csv"
            call_command("audit_corpus", "--csv", str(destination), stdout=StringIO())
            with destination.open(encoding="utf-8", newline="") as stream:
                registry = {int(entry["document_id"]): entry for entry in csv.DictReader(stream)}
        source = registry[evidence["document_id"]]
        self.assertEqual(source["url"], evidence["url"])
        self.assertEqual(source["publisher"], evidence["publisher"])
        self.assertEqual(source["original_url"], evidence["original_url"])
        self.assertEqual(source["kind"], evidence["source_kind"])
        self.assertEqual(result["evidence_strength"]["source_types"], {"official": 1})

    def test_expired_catalogued_alert_is_available_for_historical_claim(self):
        alert = Document.objects.filter(source_kind="ostrzeżenie", valid_to__isnull=False).order_by("valid_from").first()
        first_chunk = alert.chunks.first()
        with patch("verify.retrieval.embed", return_value=[first_chunk.embedding]):
            restored = search("Ostrzeżenie", 1)[0]
        self.assertEqual(restored["document_id"], alert.pk)
        now = timezone.localtime(alert.valid_to + timedelta(days=1))
        date = timezone.localtime(alert.valid_from).date()
        past_material = f"Czy {date.isoformat()} obowiązywało ostrzeżenie?"
        current_material = "Czy obecnie obowiązuje ostrzeżenie?"

        def complete(prompt, data):
            if prompt == CLAIMS_PROMPT:
                return {"claims": [{"text": data["material"], "context": None}], "truncated": False}
            if prompt == RELEVANCE_PROMPT:
                return {"chunk_ids": [restored["id"]]}
            if prompt == SYSTEM_PROMPT:
                return {**REPLY, "quote_ids": [next(iter(data["fragments"][0]["quotes"]))]}
            self.assertEqual(prompt, EVIDENCE_PROMPT)
            return {"relation": "supports", "citations": {data["citations"][0]["id"]: "supports"}}

        with patch("verify.verification.timezone.localtime", return_value=now):
            present = verify(current_material, search=lambda *_: [restored], complete=complete)
            past = verify(past_material, search=lambda *_: [restored], complete=complete)
        self.assertEqual(present["reason_code"], "period_mismatch")
        self.assertEqual(past["status"], "resolved")
        self.assertEqual(past["evidence"][0]["document_id"], alert.pk)
        self.assertIn("archiwalna", past["limitations"][0])


class AuditCorpusTests(TestCase):
    def test_check_ignores_unindexed_and_duplicate_documents(self):
        common = {"title": "Test", "fetched_at": timezone.now(), "source_kind": "komunikat"}
        first = Document.objects.create(url="https://example.org/one", content_hash="1" * 64, **common)
        duplicate = Document.objects.create(url="https://example.net/copy", content_hash="1" * 64, **common)
        partial = Document.objects.create(url="https://example.edu/partial", content_hash="2" * 64, **common)
        for document in (first, duplicate, partial):
            Chunk.objects.create(document=document, position=0, text="Test", embedding=[1.0] * 1024)
        Chunk.objects.create(document=partial, position=1, text="Fragment bez wektora")
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("audit_corpus", "--check", stdout=output)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["registered_documents"], 3)
        self.assertEqual(summary["documents"], 1)
        self.assertEqual(summary["distinct_source_domains"], 1)
        self.assertFalse(summary["meets_minima"])
