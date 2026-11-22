import json
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from .analysis import EVIDENCE_PROMPT, RELEVANCE_PROMPT, SYSTEM_PROMPT, assess
from .claims import CLAIMS_PROMPT, extract_claims
from .test_analysis import CHUNK, REPLY, REVIEW
from .verification import verify

FIRST = "Wstęp do muzeum jest bezpłatny."
SECOND = "Muzeum jest otwarte w poniedziałki."
MATERIAL = f"{FIRST} {SECOND}"
SECOND_CHUNK = {**CHUNK, "id": "43", "document_id": 11, "url": "https://example.org/godziny",
                "text": "W poniedziałki muzeum jest zamknięte."}


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="")
@override_settings(PRAWDOWID_AUDIT_ENABLED=False, PRAWDOWID_RATE_LIMIT_ENABLED=False)
class ClaimsTests(SimpleTestCase):
    def test_web_url_and_cli_keep_each_verdict_with_its_evidence(self):
        def complete(prompt, data):
            is_first = data["material"] == FIRST
            if prompt == RELEVANCE_PROMPT:
                return {"chunk_ids": ["42" if is_first else "43"]}
            if prompt == SYSTEM_PROMPT:
                if is_first:
                    return REPLY
                return {**REPLY, "label": "ZAPRZECZONE", "quote_ids": ["43.1"],
                        "class_scores": {**REPLY["class_scores"], "POTWIERDZONE": 0.1, "ZAPRZECZONE": 0.8}}
            self.assertEqual(prompt, EVIDENCE_PROMPT)
            return REVIEW if is_first else {"relation": "contradicts", "citations": {"43.1": "contradicts"}}

        extracted = {"claims": [{"text": item, "context": None} for item in (FIRST, SECOND)], "truncated": False}
        with (
            patch("verify.claims.request_completion", return_value=extracted) as extract,
            patch("verify.analysis._request_completion", side_effect=complete),
            patch("verify.verification.search_corpus", side_effect=lambda text, limit: [CHUNK if text == FIRST else SECOND_CHUNK]) as search,
            patch("verify.verification.fetch", return_value=("https://example.org/article", "Artykuł", MATERIAL)) as fetch,
        ):
            web = self.client.post("/", {"material": "https://example.org/article"})
            output = StringIO()
            call_command("verify_claim", "--text", MATERIAL, stdout=output)

        fetch.assert_called_once()
        self.assertEqual(search.call_count, 4)
        self.assertEqual([call.args[0] for call in search.call_args_list], [FIRST, SECOND] * 2)
        self.assertTrue(all(call.args[0] == CLAIMS_PROMPT for call in extract.call_args_list))
        result = json.loads(output.getvalue())
        self.assertEqual(result, web.context["result"])
        self.assertIsNone(result["verdict"])
        self.assertEqual([claim["verdict"] for claim in result["claims"]], ["true", "false"])
        self.assertEqual([claim["evidence"][0]["document_id"] for claim in result["claims"]], [10, 11])
        for text in (FIRST, SECOND, CHUNK["url"], SECOND_CHUNK["url"], 'id="dowody1"', 'id="dowody2"'):
            self.assertContains(web, text)

    def test_extraction_preserves_verbatim_context(self):
        material = "W 2024 r. opłaty zostały zniesione. Opłaty nie wróciły."
        complete = Mock(return_value={
            "claims": [{"text": "Opłaty nie wróciły.", "context": "W 2024 r."}], "truncated": False,
        })
        self.assertEqual(extract_claims(material, complete=complete),
                         ([("Opłaty nie wróciły.", "W 2024 r.")], False))

    def test_invalid_claim_fails_before_search(self):
        complete = Mock(return_value={
            "claims": [{"text": "Opłaty wróciły.", "context": None}], "truncated": False,
        })
        search = Mock()
        with self.assertLogs("verify.verification", level="WARNING"):
            result = verify("Opłaty nie wróciły.", complete=complete, search=search)
        self.assertEqual(result["status"], "technical_error")
        search.assert_not_called()

    def test_limit_is_reported_for_a_longer_submission(self):
        material = "Kwota wzrosła. Ratę obniżono. Opłatę zniesiono. Termin minął. Sprawa jest zamknięta."
        excerpts = ["Kwota wzrosła.", "Ratę obniżono.", "Opłatę zniesiono.", "Termin minął."]
        complete = Mock(return_value={
            "claims": [{"text": item, "context": None} for item in excerpts], "truncated": True,
        })
        search = Mock(return_value=[])
        result = verify(material, complete=complete, search=search)
        self.assertTrue(result["claims_truncated"])
        self.assertEqual([item["claim"] for item in result["claims"]], excerpts)
        self.assertTrue(all(item["status"] == "abstained" for item in result["claims"]))
        complete.assert_called_once()
        self.assertEqual(search.call_count, 4)

    def test_misleading_requires_support_and_verified_qualifying_context(self):
        chunk = {**CHUNK, "text": "Wstęp jest bezpłatny. Bezpłatny wstęp obowiązuje wyłącznie w niedzielę."}
        prediction = {
            **REPLY, "label": "WPROWADZAJĄCE W BŁĄD", "quote_ids": ["42.1", "42.2"],
            "class_scores": {"POTWIERDZONE": 0.1, "ZAPRZECZONE": 0.1,
                             "NIEZWERYFIKOWANE": 0.1, "WPROWADZAJĄCE W BŁĄD": 0.7},
        }
        for second, expected in (("qualifies", "resolved"), ("context", "abstained"), ("contradicts", "abstained")):
            with self.subTest(second=second):
                review = {"relation": "misleading", "citations": {"42.1": "supports", "42.2": second}}
                result = assess("Wstęp jest darmowy każdego dnia.", [chunk],
                                complete=Mock(side_effect=[prediction, review]))
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["verdict"], "misleading" if expected == "resolved" else None)

    def test_unreadable_ranking_falls_back_to_candidates_with_evidence_review(self):
        from json import JSONDecodeError

        complete = Mock(side_effect=[
            {"claims": [{"text": FIRST, "context": None}], "truncated": False},
            JSONDecodeError("Invalid JSON", "", 0),
            REPLY, REVIEW,
        ])
        with self.assertLogs("verify.verification", level="WARNING"):
            result = verify(FIRST, complete=complete, search=lambda *_: [CHUNK])
        self.assertEqual(result["verdict"], "true")
        self.assertEqual(complete.call_args.args[0], EVIDENCE_PROMPT)

    def test_model_adapter_accepts_json_fences(self):
        from .providers import request_completion

        with (
            patch.dict("os.environ", PRAWDOWID_LLM_URL=""),
            patch("verify.providers._local_completion", return_value='```json\n{"claims": [], "truncated": false}\n```'),
        ):
            self.assertEqual(extract_claims("Czy to prawda?", complete=request_completion), ([], False))

    def test_citation_review_sees_a_condition_in_nearby_source_text(self):
        chunk = {**CHUNK, "text": "Park oferuje bezpłatny wstęp. Wstęp bez biletu przysługuje tylko dzieciom."}
        complete = Mock(side_effect=[REPLY, {"relation": "context", "citations": {"42.1": "context"}}])
        result = assess("Park jest dostępny bez biletu.", [chunk], complete=complete)
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(complete.call_args.args[1]["source_context"], {"42": chunk["text"]})
        self.assertNotIn("source_context", result["evidence"][0])
