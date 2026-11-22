import json
from datetime import datetime
from io import StringIO
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from .evidence_strength import evidence_strength
from .test_analysis import CHUNK, REPLY
from .verification import verify

NOW = datetime(2026, 11, 22, 12, tzinfo=ZoneInfo("Europe/Warsaw"))
DATED = {**CHUNK, "url": "https://www.gov.pl/dokument", "published_at": "2026-11-22"}
EXTRACTED = {"claims": [{"text": "Teza faktograficzna.", "context": None}], "truncated": False}


class EvidenceStrengthTests(SimpleTestCase):
    def test_counts_documents_and_conservatively_groups_sources(self):
        documents = [
            {**DATED, "relation": "supports", "quote": "Cytat A"},
            {**DATED, "relation": "supports", "quote": "Cytat B"},
            {**DATED, "relation": "supports", "quote": "Cytat B",
             "document_id": 11, "url": "https://example.org/przedruk"},
            {**DATED, "relation": "supports", "quote": "Cytat C",
             "document_id": 12, "url": "https://example.org/drugi-tekst"},
        ]
        for items in (documents, list(reversed(documents))):
            with self.subTest(items=items):
                strength = evidence_strength(
                    {"status": "resolved", "verdict": "true", "evidence": items},
                    now=NOW, period=(NOW, NOW),
                )
                self.assertEqual(strength["documents"], 3)
                self.assertEqual(strength["direct_citations"], 4)
                self.assertEqual(strength["source_groups_in_agreement"], 1)
                self.assertEqual(strength["level"], "moderate")

    def test_direct_current_sources_strengthen_the_basis(self):
        evidence = [
            {**DATED, "relation": "contradicts", "quote": "Cytat urzędowy"},
            {**DATED, "relation": "contradicts", "quote": "Niezależne dane",
             "document_id": 12, "url": "https://example.org/badanie", "source_type": "primary"},
            {**DATED, "relation": "context", "quote": "Tło"},
        ]
        result = {"status": "resolved", "verdict": "false", "evidence": evidence}
        strength = evidence_strength(result, now=NOW, period=(NOW, NOW))
        self.assertEqual(strength["level"], "strong")
        self.assertEqual(strength["source_groups_in_agreement"], 2)
        self.assertEqual(strength["source_types"], {"official": 1, "primary": 1})

        evidence[1]["published_at"] = None
        strength = evidence_strength(result, now=NOW, period=(NOW, NOW))
        self.assertEqual(strength["level"], "limited")
        self.assertEqual(strength["temporal_fit"], "unconfirmed")


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="")
@override_settings(PRAWDOWID_AUDIT_ENABLED=False)
class EvidenceOutcomeTests(SimpleTestCase):
    def test_factual_claim_without_results_never_receives_scope_verdict(self):
        complete = Mock(return_value=EXTRACTED)
        result = verify("Teza faktograficzna.", search=lambda *_: [], complete=complete)
        complete.assert_called_once()
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason_code"], "no_sources")
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["evidence_strength"]["level"], "insufficient")

    def test_conflicting_sources_show_both_citations_without_verdict(self):
        second = {**DATED, "id": "43", "document_id": 11, "text": "Treść drugiego raportu."}
        complete = Mock(side_effect=[
            EXTRACTED, {"chunk_ids": ["42", "43"]}, {**REPLY, "quote_ids": ["42.1", "43.1"]},
            {"relation": "context", "citations": {"42.1": "supports", "43.1": "contradicts"}},
        ])
        result = verify("Teza faktograficzna.", search=lambda *_: [DATED, second], complete=complete)
        self.assertEqual(result["reason_code"], "conflicting_evidence")
        self.assertIsNone(result["verdict"])
        self.assertEqual([item["relation"] for item in result["evidence"]], ["supports", "contradicts"])
        self.assertEqual(result["evidence_strength"]["level"], "insufficient")

    def test_cli_reports_article_limit_as_material_unavailable(self):
        output = StringIO()
        with patch("verify.verification.fetch", return_value=("https://example.org", "A", "a" * 12001)):
            call_command("verify_claim", "--url", "https://example.org", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "material_unavailable")
        self.assertEqual(result["reason_code"], "article_too_long")
        self.assertIsNone(result["verdict"])
        self.assertNotIn("evidence_strength", result)
