import json
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from .analysis import INTENT_PROMPT
from .test_analysis import CHUNK, REPLY, REVIEW
from .verification import verify


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="")
class VerificationArchitectureTests(SimpleTestCase):
    @patch("torch.cuda.empty_cache")
    @patch("torch.cuda.is_initialized", return_value=True)
    def test_cuda_cache_is_released_after_verification(self, initialized, empty_cache):
        complete = Mock(side_effect=[{"chunk_ids": ["42"]}, REPLY, REVIEW])
        empty_cache.side_effect = lambda: self.assertEqual(complete.call_count, 3)
        result = verify("Twierdzenie", search=lambda *_: [CHUNK], complete=complete)
        self.assertEqual(result["status"], "resolved")
        empty_cache.assert_called_once_with()

        empty_cache.reset_mock(side_effect=True)
        empty_cache.side_effect = RuntimeError
        with self.assertLogs("verify.verification", level="WARNING"):
            result = verify("Twierdzenie", search=lambda *_: [],
                            complete=Mock(return_value={"status": "checkable"}))
        self.assertEqual(result["status"], "abstained")
        empty_cache.assert_called_once_with()

    @patch("torch.cuda.empty_cache")
    @patch("torch.cuda.is_initialized", return_value=False)
    def test_cpu_verification_skips_cuda_cache_cleanup(self, initialized, empty_cache):
        result = verify("Twierdzenie", search=lambda *_: [],
                        complete=Mock(return_value={"status": "checkable"}))
        self.assertEqual(result["status"], "abstained")
        empty_cache.assert_not_called()

    def test_clarification_is_checked_against_the_submission_without_source_context(self):
        clarification = {"status": "needs_clarification", "label": None, "class_scores": None, "quote_ids": []}
        for text, intent, expected in (
            ("Czy we Włoszech ostatnio odbywał się strajk generalny?", "checkable", "abstained"),
            ("Czy to prawda?", "needs_clarification", "needs_clarification"),
        ):
            with self.subTest(text=text):
                complete = Mock(side_effect=[{"chunk_ids": []}, clarification, {"status": intent}])
                result = verify(text, search=lambda *_: [CHUNK], complete=complete)
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["verdict"])
                self.assertEqual(complete.call_args.args, (INTENT_PROMPT, {"material": text}))

    def test_switching_provider_configuration_preserves_result_contract(self):
        replies = [{"chunk_ids": ["42"]}, REPLY, REVIEW]
        search = Mock(return_value=[CHUNK])
        with (
            patch.dict("os.environ", PRAWDOWID_LLM_URL="", PRAWDOWID_LLM_MODEL="local-test", RAG_TOP_K="5"),
            patch("verify.providers._local_completion", side_effect=map(json.dumps, replies)) as local,
            patch("verify.providers._remote_completion", side_effect=replies) as remote,
        ):
            local_result = verify("Twierdzenie", search=search)
            remote.assert_not_called()
            with patch.dict("os.environ", PRAWDOWID_LLM_URL="https://example.org/chat", PRAWDOWID_LLM_MODEL="remote-test"):
                remote_result = verify("Twierdzenie", search=search)

        self.assertEqual(local_result["status"], "resolved")
        self.assertEqual(local_result, remote_result)
        self.assertEqual(local.call_count, 3)
        self.assertEqual(local.call_args.args[0], "local-test")
        self.assertEqual(remote.call_count, 3)
        self.assertEqual(remote.call_args.args[:2], ("https://example.org/chat", "remote-test"))
        self.assertEqual([call.args for call in search.call_args_list], [("Twierdzenie", 15)] * 2)

    @patch("verify.verification.search_corpus", return_value=[CHUNK])
    @patch("verify.verification.fetch", return_value=("https://example.org/article", "Artykuł", "Twierdzenie"))
    @patch("verify.analysis._request_completion")
    def test_web_and_cli_use_same_verification_result(self, complete, fetch, search):
        complete.side_effect = [{"chunk_ids": ["42"]}, REPLY, REVIEW] * 4
        for mode, value in (("text", "Twierdzenie"), ("url", "https://example.org/article")):
            with self.subTest(mode=mode):
                web = self.client.post("/", {"material": value})
                output = StringIO()
                call_command("verify_claim", f"--{mode}", value, stdout=output)
                self.assertEqual(web.context["result"]["status"], "resolved")
                self.assertEqual(json.loads(output.getvalue()), web.context["result"])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(search.call_count, 4)
