from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .analysis import EVIDENCE_PROMPT, INTENT_PROMPT, RELEVANCE_PROMPT, SYSTEM_PROMPT
from .fetch import SourceUnavailable

ABSTENTION = {"status": "abstained", "label": None, "class_scores": None, "quote_ids": []}


class VerificationPageTests(SimpleTestCase):
    def test_form_available(self):
        response = self.client.get("/")
        self.assertContains(response, "Sprawdź informację")
        self.assertNotContains(response, "Czas weryfikacji")

    @patch("verify.views.perf_counter", side_effect=[10, 11.25])
    @patch("verify.verification.search_corpus", return_value=[])
    @patch("verify.verification.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    @patch("verify.analysis._request_completion", return_value={"status": "checkable"})
    def test_url_uses_extracted_text(self, complete, fetch, retrieve, clock):
        response = self.client.post("/", {"mode": "url", "material": "https://example.org/"})
        retrieve.assert_called_once()
        self.assertEqual(retrieve.call_args.args[0], "Treść materiału")
        complete.assert_called_once_with(INTENT_PROMPT, {"material": "Treść materiału"})
        self.assertContains(response, 'data-status="abstained"')
        self.assertContains(response, "brakuje wystarczających dowodów")
        self.assertIsNone(response.context["result"]["verdict"])
        self.assertNotContains(response, "Szacunki modelu dla klas")
        self.assertContains(response, "Czas weryfikacji <strong>1,25 s</strong>")

    @patch("verify.verification.search_corpus")
    def test_invalid_url_stops_before_retrieval(self, retrieve):
        response = self.client.post("/", {"mode": "url", "material": "file:///example"})
        self.assertTrue(response.context["form"].errors)
        retrieve.assert_not_called()

    @patch("verify.verification.search_corpus")
    @patch("verify.verification.fetch", side_effect=SourceUnavailable("Strona nie zawiera dostępnego tekstu."))
    def test_unavailable_url_abstains(self, fetch, retrieve):
        response = self.client.post("/", {"mode": "url", "material": "https://example.org/"})
        self.assertContains(response, 'data-status="abstained"')
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
                response = self.client.post("/", {"mode": "text", "material": text})
                self.assertContains(response, f'data-status="{status}"')
                self.assertContains(response, f"<h3>{heading}</h3>", html=True)
                self.assertIsNone(response.context["result"]["verdict"])
                self.assertNotContains(response, "sensitive details")

    @patch("verify.verification.search_corpus", side_effect=RuntimeError)
    def test_retrieval_failure_returns_technical_error(self, retrieve):
        with self.assertLogs("verify.verification", level="WARNING"):
            response = self.client.post("/", {"mode": "text", "material": "RSO jest darmowy."})
        self.assertContains(response, 'data-status="technical_error"')
        self.assertContains(response, "Wyszukiwanie źródeł jest chwilowo niedostępne.")
        self.assertIsNone(response.context["result"]["verdict"])

    @patch("verify.verification.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    @patch("verify.retrieval.embed", return_value=[[1.0, 0.0]])
    @patch("verify.retrieval.Chunk.objects")
    @patch("verify.analysis._request_completion")
    def test_both_inputs_assess_only_selected_candidates(self, complete, objects, embed, fetch):
        material = "Treść materiału"
        document = SimpleNamespace(
            url="https://example.org/source",
            title="Komunikat",
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at=None, valid_from=None, valid_to=None,
        )
        texts = ["Fragment na podobny temat.", "Dowód za.", "Dowód przeciw."]
        rows = [
            SimpleNamespace(pk=pk, document_id=10, position=pk - 1, text=text, document=document)
            for pk, text in enumerate(texts, start=1)
        ]
        indexed_chunks = objects.filter.return_value
        indexed_chunks.exists.return_value = True
        query = indexed_chunks.select_related.return_value.order_by.return_value
        query.__getitem__.side_effect = rows.__getitem__

        def respond(prompt, data):
            self.assertEqual(data["material"], material)
            if prompt == RELEVANCE_PROMPT:
                self.assertEqual([item["id"] for item in data["candidates"]], ["1", "2", "3"])
                return {"chunk_ids": ["3", "2"]}
            if prompt == EVIDENCE_PROMPT:
                self.assertEqual([item["id"] for item in data["citations"]], ["3.1", "2.1"])
                return {"relation": "supports", "citations": {"3.1": "contradicts", "2.1": "supports"}}
            self.assertEqual(prompt, SYSTEM_PROMPT)
            self.assertEqual([list(item["quotes"]) for item in data["fragments"]], [["3.1"], ["2.1"]])
            return {
                "status": "resolved",
                "label": "POTWIERDZONE",
                "class_scores": {"POTWIERDZONE": 0.8, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.1},
                "quote_ids": ["3.1", "2.1"],
            }

        complete.side_effect = respond
        with patch.dict("os.environ", RAG_TOP_K="2"):
            for mode, value in (("text", material), ("url", "https://example.org/")):
                with self.subTest(mode=mode):
                    response = self.client.post("/", {"mode": mode, "material": value})
                    self.assertContains(response, 'data-status="abstained"')
                    self.assertContains(response, "Źródła zawierają sprzeczne dowody.")
                    self.assertContains(response, "Dowód za.")
                    self.assertContains(response, "Dowód przeciw.")
                    self.assertContains(response, document.url)
                    self.assertNotContains(response, rows[0].text)
        self.assertEqual(complete.call_count, 6)
        self.assertEqual(embed.call_count, 2)
        fetch.assert_called_once_with("https://example.org/")

        complete.reset_mock()
        complete.side_effect = [
            {"chunk_ids": []},
            {
                "status": "resolved",
                "label": "POTWIERDZONE",
                "class_scores": {"POTWIERDZONE": 0.8, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.1},
                "quote_ids": ["2.1"],
            },
            {"relation": "supports", "citations": {"2.1": "supports"}},
            {"chunk_ids": []},
            ABSTENTION,
        ]
        with patch.dict("os.environ", RAG_TOP_K="2"):
            recovered = self.client.post("/", {"mode": "text", "material": material})
            unverified = self.client.post("/", {"mode": "text", "material": "Inne twierdzenie"})
        self.assertEqual(complete.call_count, 5)
        for call in (complete.call_args_list[1], complete.call_args_list[4]):
            prompt, data = call.args
            self.assertEqual(prompt, SYSTEM_PROMPT)
            self.assertEqual([list(item["quotes"]) for item in data["fragments"]], [["1.1"], ["2.1"]])
        self.assertContains(recovered, "<h3>Potwierdzone</h3>", html=True)
        self.assertContains(recovered, 'data-status="resolved"')
        self.assertContains(recovered, f'<a href="{document.url}" rel="noopener noreferrer">{document.title}</a>', html=True)
        self.assertContains(recovered, "Dokument 10, fragment 2.")
        self.assertContains(recovered, "Data publikacji nieznana.")
        self.assertContains(recovered, "Szacunki są nieskalibrowane.")
        self.assertContains(recovered, "Dowód za.")
        self.assertContains(unverified, "<h3>Brak rozstrzygnięcia</h3>", html=True)
        self.assertContains(unverified, "brakuje wystarczających dowodów")
        self.assertNotContains(unverified, "Dowody ze źródeł")
