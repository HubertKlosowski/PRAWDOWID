from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .analysis import RELEVANCE_PROMPT, SYSTEM_PROMPT


class VerificationPageTests(SimpleTestCase):
    def test_form_available(self):
        response = self.client.get("/")
        self.assertContains(response, "Sprawdź informację")
        self.assertNotContains(response, "Czas weryfikacji")

    @patch("verify.views.perf_counter", side_effect=[10, 11.25])
    @patch("verify.views.retrieve", return_value=[])
    @patch("verify.views.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    def test_url_uses_extracted_text(self, fetch, retrieve, clock):
        response = self.client.post("/", {"mode": "url", "material": "https://example.org/"})
        retrieve.assert_called_once_with("Treść materiału")
        self.assertContains(response, "Niezweryfikowane")
        self.assertContains(response, "W korpusie nie znaleziono")
        self.assertContains(response, "Czas weryfikacji <strong>1,25 s</strong>")

    @patch("verify.views.retrieve")
    def test_invalid_url_stops_before_retrieval(self, retrieve):
        response = self.client.post("/", {"mode": "url", "material": "file:///example"})
        self.assertTrue(response.context["form"].errors)
        retrieve.assert_not_called()

    @patch("verify.views.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    @patch("verify.retrieval.embed", return_value=[[1.0, 0.0]])
    @patch("verify.retrieval.Chunk.objects")
    @patch("verify.analysis._request_completion")
    def test_both_inputs_assess_only_selected_candidates(self, complete, objects, embed, fetch):
        material = "Treść materiału"
        document = SimpleNamespace(
            url="https://example.org/source",
            title="Komunikat",
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        texts = ["Fragment na podobny temat.", "Dowód za.", "Dowód przeciw."]
        rows = [
            SimpleNamespace(pk=pk, text=text, document=document)
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
            self.assertEqual(prompt, SYSTEM_PROMPT)
            self.assertEqual([list(item["quotes"]) for item in data["fragments"]], [["3.1"], ["2.1"]])
            return {
                "label": "NIEZWERYFIKOWANE",
                "class_scores": {"POTWIERDZONE": 0.1, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.8},
                "explanation": "Źródła są sprzeczne.",
                "evidence": [
                    {"quote_id": "3.1", "relation": "contradicts"},
                    {"quote_id": "2.1", "relation": "supports"},
                ],
            }

        complete.side_effect = respond
        with patch.dict("os.environ", RAG_TOP_K="2"):
            for mode, value in (("text", material), ("url", "https://example.org/")):
                with self.subTest(mode=mode):
                    response = self.client.post("/", {"mode": mode, "material": value})
                    self.assertContains(response, "Dowód za.")
                    self.assertContains(response, "Dowód przeciw.")
                    self.assertContains(response, document.url)
                    self.assertNotContains(response, rows[0].text)
        self.assertEqual(complete.call_count, 4)
        self.assertEqual(embed.call_count, 2)
        fetch.assert_called_once_with("https://example.org/")

        complete.reset_mock()
        complete.side_effect = [
            {"chunk_ids": []},
            {
                "label": "POTWIERDZONE",
                "class_scores": {"POTWIERDZONE": 0.8, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.1},
                "explanation": "Bezpośredni dowód w komunikacie.",
                "evidence": [{"quote_id": "2.1", "relation": "supports"}],
            },
            {"chunk_ids": []},
            {
                "label": "NIEZWERYFIKOWANE",
                "class_scores": {"POTWIERDZONE": 0.1, "ZAPRZECZONE": 0.1, "NIEZWERYFIKOWANE": 0.8},
                "explanation": "Brak dowodu dotyczącego tego twierdzenia.",
                "evidence": [],
            },
        ]
        with patch.dict("os.environ", RAG_TOP_K="2"):
            recovered = self.client.post("/", {"mode": "text", "material": material})
            unverified = self.client.post("/", {"mode": "text", "material": "Inne twierdzenie"})
        self.assertEqual(complete.call_count, 4)
        for call in complete.call_args_list[1::2]:
            prompt, data = call.args
            self.assertEqual(prompt, SYSTEM_PROMPT)
            self.assertEqual([list(item["quotes"]) for item in data["fragments"]], [["1.1"], ["2.1"]])
        self.assertContains(recovered, "<h3>Potwierdzone</h3>", html=True)
        self.assertContains(recovered, "Dowód za.")
        self.assertContains(unverified, "<h3>Niezweryfikowane</h3>", html=True)
        self.assertContains(unverified, "Brak dowodu dotyczącego tego twierdzenia.")
        self.assertNotContains(unverified, "Dowody ze źródeł")
