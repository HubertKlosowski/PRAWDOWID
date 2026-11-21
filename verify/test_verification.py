import json
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from .test_analysis import CHUNK, REPLY, REVIEW
from .verification import verify


class VerificationArchitectureTests(SimpleTestCase):
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
                web = self.client.post("/", {"mode": mode, "material": value})
                output = StringIO()
                call_command("verify_claim", f"--{mode}", value, stdout=output)
                self.assertEqual(web.context["result"]["status"], "resolved")
                self.assertEqual(json.loads(output.getvalue()), web.context["result"])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(search.call_count, 4)
