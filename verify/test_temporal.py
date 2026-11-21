from datetime import date, datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .analysis import RELEVANCE_PROMPT
from .fetch import extract
from .test_analysis import CHUNK, REPLY, REVIEW
from .verification import verify


NOW = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo("Europe/Warsaw"))
OLD = {
    **CHUNK,
    "text": "W Krakowie obowiązuje ostrzeżenie.",
    "published_at": "2025-11-21",
    "valid_from": "2025-11-21T10:00:00+01:00",
    "valid_to": "2025-11-21T18:00:00+01:00",
    "fetched_at": NOW.isoformat(),
}


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="")
class TemporalVerificationTests(SimpleTestCase):
    @patch("verify.verification.timezone.localtime", return_value=NOW)
    def test_recent_retrieval_does_not_make_expired_alert_current(self, clock):
        complete = Mock()
        result = verify("Czy dziś obowiązuje ostrzeżenie w Krakowie?",
                        search=lambda text, limit: [OLD], complete=complete)
        self.assertEqual(result["status"], "abstained")
        self.assertIsNone(result["verdict"])
        self.assertIn("archiwalna", result["limitations"][0])
        complete.assert_not_called()

    @patch("verify.verification.timezone.localtime", return_value=NOW)
    def test_historical_claim_prefers_evidence_from_its_period(self, clock):
        current = {**OLD, "id": "7", "published_at": "2026-11-21",
                   "valid_from": "2026-11-21T10:00:00+01:00", "valid_to": "2026-11-21T18:00:00+01:00"}
        retrospective = {**CHUNK, "id": "8", "published_at": "2026-01-01"}

        complete = Mock(side_effect=[{"chunk_ids": []}, REPLY, REVIEW])
        result = verify("Czy 21 listopada 2025 w Krakowie obowiązywało ostrzeżenie?",
                        search=lambda text, limit: [current, retrospective, OLD], complete=complete)
        prompt, data = complete.call_args_list[0].args
        self.assertEqual(prompt, RELEVANCE_PROMPT)
        self.assertEqual([item["id"] for item in data["candidates"]], ["42", "8"])
        self.assertEqual(data["period"]["from"], "2025-11-21T00:00:00+01:00")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["evidence"][0]["published_at"], "2025-11-21")
        self.assertEqual(result["evidence"][0]["retrieved_at"], NOW.isoformat())
        self.assertIn("archiwalna", result["limitations"][0])

    def test_html_publication_does_not_use_modification_date(self):
        html = b'<meta property="article:modified_time" content="2026-11-21"><main>Komunikat</main>'
        self.assertIsNone(extract(html, "text/html", None, "Test", with_dates=True)[2]["published_at"])
        html += b'<meta property="article:published_time" content="2025-11-21T10:01:00+01:00">'
        self.assertEqual(extract(html, "text/html", None, "Test", with_dates=True)[2]["published_at"],
                         date(2025, 11, 21))
