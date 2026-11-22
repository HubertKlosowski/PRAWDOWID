import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .analysis import EVIDENCE_PROMPT, SYSTEM_PROMPT
from .fetch import SourceUnavailable

ABSTENTION = {"status": "abstained", "label": None, "class_scores": None, "quote_ids": []}


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="")
@patch("verify.verification.extract_claims", new=lambda material, **kwargs: ([(" ".join(material.split()), None)], False))
@override_settings(PRAWDOWID_AUDIT_ENABLED=False, PRAWDOWID_RATE_LIMIT_ENABLED=False)
class VerificationPageTests(SimpleTestCase):
    def test_form_available(self):
        response = self.client.get("/")
        self.assertContains(response, "Sprawdź informację")
        self.assertContains(response, "Wklej tekst lub adres strony")
        self.assertNotContains(response, 'name="mode"')
        self.assertNotContains(response, "Czas weryfikacji")

    @patch("verify.views.perf_counter", side_effect=[10, 11.25])
    @patch("verify.verification.search_corpus", return_value=[])
    @patch("verify.verification.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    @patch("verify.analysis._request_completion", return_value={"status": "checkable"})
    def test_url_uses_extracted_text(self, complete, fetch, retrieve, clock):
        url = "HTTPS://example.org/?id=1"
        response = self.client.post("/", {"material": f"  {url}\n"})
        fetch.assert_called_once_with(url)
        retrieve.assert_called_once()
        self.assertEqual(retrieve.call_args.args[0], "Treść materiału")
        complete.assert_not_called()
        self.assertContains(response, 'data-status="abstained"')
        self.assertContains(response, "W korpusie nie znaleziono źródeł")
        self.assertIsNone(response.context["result"]["verdict"])
        self.assertNotContains(response, "Szacunki modelu dla klas")
        self.assertContains(response, "Czas weryfikacji <strong>1,25 s</strong>")

    @patch("verify.verification.search_corpus")
    def test_invalid_url_stops_before_retrieval(self, retrieve):
        response = self.client.post("/", {"material": "https:///example"})
        self.assertTrue(response.context["form"].errors)
        retrieve.assert_not_called()

    @patch("verify.verification.fetch")
    @patch("verify.verification.search_corpus", return_value=[])
    @patch("verify.analysis._request_completion", return_value={"status": "checkable"})
    def test_urls_in_text_do_not_trigger_fetch(self, complete, retrieve, fetch):
        for text in (
            "Sprawdź wiadomość opisaną na https://example.org/",
            "https://example.org/ podaje, że zamknięto drogę.",
            "https://example.org/\nhttps://example.com/",
            "file:///example",
        ):
            with self.subTest(text=text):
                response = self.client.post("/", {"material": text})
                self.assertContains(response, 'data-status="abstained"')
                self.assertEqual(retrieve.call_args.args[0], text)
        fetch.assert_not_called()

    @patch("verify.verification.search_corpus")
    @patch("verify.verification.fetch", side_effect=SourceUnavailable("Strona nie zawiera dostępnego tekstu."))
    def test_unavailable_url_has_its_own_status(self, fetch, retrieve):
        response = self.client.post("/", {"material": "https://example.org/"})
        self.assertContains(response, 'data-status="material_unavailable"')
        self.assertContains(response, "<h3>Materiał niedostępny</h3>", html=True)
        self.assertEqual(response.context["result"]["reason_code"], "article_unreadable")
        self.assertContains(response, "Strona nie zawiera dostępnego tekstu.")
        self.assertIsNone(response.context["result"]["verdict"])
        retrieve.assert_not_called()

    @patch("verify.verification.search_corpus", return_value=[])
    @patch("verify.analysis._request_completion")
    def test_clarification_out_of_scope_and_model_failure_have_distinct_messages(self, complete, retrieve):
        complete.side_effect = [
            {"status": "needs_clarification"},
            {"status": "out_of_scope"},
            TimeoutError("sensitive details"),
        ]
        for text, status, heading in (
            ("Czy to prawda?", "needs_clarification", "Doprecyzuj zgłoszenie"),
            ("Napisz wiersz.", "out_of_scope", "Poza zakresem weryfikacji"),
            ("RSO jest darmowy.", "technical_error", "Błąd techniczny"),
        ):
            with self.subTest(status=status):
                with patch("verify.verification.extract_claims", return_value=([], False)):
                    response = self.client.post("/", {"material": text})
                self.assertContains(response, f'data-status="{status}"')
                self.assertContains(response, f"<h3>{heading}</h3>", html=True)
                self.assertIsNone(response.context["result"]["verdict"])
                self.assertNotContains(response, "sensitive details")

    @patch("verify.verification.search_corpus", side_effect=RuntimeError)
    def test_retrieval_failure_returns_technical_error(self, retrieve):
        with self.assertLogs("verify.verification", level="WARNING"):
            response = self.client.post("/", {"material": "RSO jest darmowy."})
        self.assertContains(response, 'data-status="technical_error"')
        self.assertContains(response, "Wyszukiwanie źródeł jest chwilowo niedostępne.")
        self.assertIsNone(response.context["result"]["verdict"])

    @patch("verify.verification.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    @patch("verify.retrieval.embed", return_value=[[1.0, 0.0]])
    @patch("verify.retrieval.Chunk.objects")
    @patch("verify.analysis._request_completion")
    def test_both_inputs_assess_the_top_two_candidates_by_default(self, complete, objects, embed, fetch):
        material = "Treść materiału"
        document = SimpleNamespace(
            url="https://example.org/source",
            title="Komunikat",
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at=None, valid_from=None, valid_to=None,
            original_url="", source_domain="", source_type="official", source_kind="komunikat", publisher="Testowy urząd",
        )
        texts = ["Fragment na podobny temat.", "Dowód za.", "Dowód przeciw."]
        rows = [
            SimpleNamespace(pk=pk, document_id=10, position=pk - 1, text=text, document=document)
            for pk, text in enumerate(texts, start=1)
        ]
        indexed_chunks = objects.filter.return_value
        indexed_chunks.exists.return_value = True
        query = indexed_chunks.select_related.return_value.order_by.return_value
        query.__getitem__.side_effect = [rows[2], rows[1], rows[0]].__getitem__

        def respond(prompt, data):
            self.assertEqual(data["material"], material)
            if prompt == EVIDENCE_PROMPT:
                self.assertEqual([item["id"] for item in data["citations"]], ["3.1", "2.1"])
                return {"relation": "supports", "citations": {"3.1": "contradicts", "2.1": "supports"}}
            self.assertEqual(prompt, SYSTEM_PROMPT)
            self.assertEqual([list(item["quotes"]) for item in data["fragments"]], [["3.1"], ["2.1"]])
            return {
                "status": "resolved",
                "label": "POTWIERDZONE",
                "class_scores": {"POTWIERDZONE": 0.8, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.1, "WPROWADZAJĄCE W BŁĄD": 0.0},
                "quote_ids": ["3.1", "2.1"],
            }

        complete.side_effect = respond
        with patch.dict("os.environ"):
            os.environ.pop("RAG_TOP_K", None)
            for mode, value in (("text", material), ("url", "https://example.org/")):
                with self.subTest(mode=mode):
                    response = self.client.post("/", {"material": value})
                    self.assertContains(response, 'data-status="abstained"')
                    self.assertContains(response, "Źródła zawierają sprzeczne dowody.")
                    self.assertContains(response, "Dowód za.")
                    self.assertContains(response, "Dowód przeciw.")
                    self.assertContains(response, document.url)
                    self.assertNotContains(response, rows[0].text)
        self.assertEqual(complete.call_count, 4)
        self.assertEqual([call.args for call in query.__getitem__.call_args_list], [(slice(None, 6),)] * 2)
        self.assertEqual(embed.call_count, 2)
        fetch.assert_called_once_with("https://example.org/")

        complete.reset_mock()
        query.__getitem__.side_effect = rows.__getitem__
        complete.side_effect = [
            {
                "status": "resolved",
                "label": "POTWIERDZONE",
                "class_scores": {"POTWIERDZONE": 0.8, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.1, "WPROWADZAJĄCE W BŁĄD": 0.0},
                "quote_ids": ["2.1"],
            },
            {"relation": "supports", "citations": {"2.1": "supports"}},
            ABSTENTION,
        ]
        with patch.dict("os.environ", RAG_TOP_K="2"):
            resolved = self.client.post("/", {"material": material})
            unverified = self.client.post("/", {"material": "Inne twierdzenie"})
        self.assertEqual(complete.call_count, 3)
        for call in (complete.call_args_list[0], complete.call_args_list[2]):
            prompt, data = call.args
            self.assertEqual(prompt, SYSTEM_PROMPT)
            self.assertEqual([list(item["quotes"]) for item in data["fragments"]], [["1.1"], ["2.1"]])
        self.assertContains(resolved, "<h3>Potwierdzone</h3>", html=True)
        self.assertContains(resolved, 'data-status="resolved"')
        self.assertContains(resolved, f'<a href="{document.url}" rel="noopener noreferrer">{document.title}</a>', html=True)
        self.assertContains(resolved, "Dokument 10, fragment 2.")
        self.assertContains(resolved, "Data publikacji nieznana.")
        self.assertContains(resolved, "Testowy urząd")
        self.assertContains(resolved, "Szacunki są nieskalibrowane.")
        self.assertContains(resolved, "Siła dowodów: Umiarkowana")
        self.assertContains(resolved, "Dowód za.")
        self.assertContains(unverified, "<h3>Brak rozstrzygnięcia</h3>", html=True)
        self.assertContains(unverified, "Znalezione materiały nie dostarczyły cytatu")
        self.assertNotContains(unverified, "Dowody ze źródeł")
