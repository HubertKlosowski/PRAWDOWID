"""Acceptance checks for the channels using the frozen demonstration corpus."""

import json
import os
import socket
from importlib.util import find_spec
from io import StringIO
from unittest import skipUnless
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import Client, TestCase, TransactionTestCase

from .analysis import EVIDENCE_PROMPT, SYSTEM_PROMPT
from .claims import CLAIMS_PROMPT
from .corpus import load_demo_corpus
from .fetch import extract
from .models import Chunk, VerificationRun

RSO_URL = "https://www.gov.pl/web/mswia/regionalny-system-ostrzegania"
SUPPORTED = "Regionalny System Ostrzegania jest darmowy."
UNSUPPORTED = "Muzeum tego miasta mieści się na Księżycu."
ARTICLE_URL = "https://example.org/artykul"


def result_signature(result):
    return result["status"], result.get("claims_truncated", False), [
        (
            item.get("claim"), item.get("claim_context"),
            item["status"], item.get("verdict"), item.get("reason_code"),
            sorted(
                (e["document_id"], e["chunk_id"], e["id"], e["relation"])
                for e in item["evidence"]
            ),
        )
        for item in result.get("claims", [result])
    ]


def complete_fixture(name, messages):
    prompt = messages[0]["content"]
    data = json.loads(messages[1]["content"])
    material = data["material"]
    if prompt == CLAIMS_PROMPT:
        texts = [text for text in (SUPPORTED, UNSUPPORTED) if text in material]
        reply = {"claims": [{"text": text, "context": None} for text in texts], "truncated": False}
    elif prompt == SYSTEM_PROMPT and material == SUPPORTED:
        quote_id = next(
            key for part in data["fragments"] for key, value in part["quotes"].items()
            if "darmowa, powszechna usługa" in value
        )
        reply = {"status": "resolved", "label": "POTWIERDZONE", "quote_ids": [quote_id],
                 "class_scores": {"POTWIERDZONE": 1.0, "ZAPRZECZONE": 0.0,
                                  "WPROWADZAJĄCE W BŁĄD": 0.0, "NIEZWERYFIKOWANE": 0.0}}
    elif prompt == SYSTEM_PROMPT:
        reply = {"status": "abstained", "label": None, "class_scores": None, "quote_ids": []}
    elif prompt == EVIDENCE_PROMPT:
        reply = {"relation": "supports", "citations": {item["id"]: "supports" for item in data["citations"]}}
    else:
        raise AssertionError("Nieoczekiwany etap weryfikacji.")
    return json.dumps(reply)


def rso_chunk():
    return Chunk.objects.get(document__url=RSO_URL, text__contains="darmowa, powszechna usługa")


class CorpusChannelChecks(TestCase):
    @classmethod
    def setUpTestData(cls):
        load_demo_corpus()

    def compare_channels(self, material, mode):
        # Exercise the same cookie and CSRF exchange as a browser.
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/").status_code, 200)
        web = client.post("/", {"material": material, "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
        self.assertEqual(web.status_code, 200)
        self.assertIsNotNone(web.context["result"])
        output = StringIO()
        call_command("verify_claim", f"--{mode}", material, stdout=output)
        cli = json.loads(output.getvalue())
        result = web.context["result"]
        self.assertNotEqual(result["request_id"], cli["request_id"])
        self.assertEqual(result_signature(result), result_signature(cli))
        self.assertFalse(result.get("audit_unavailable"))
        self.assertFalse(cli.get("audit_unavailable"))
        for item in result.get("claims", [result]):
            for evidence in item["evidence"]:
                self.assertContains(web, f'href="{evidence["url"]}"')
        return result


@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="", PRAWDOWID_LLM_URL="", RAG_TOP_K="1")
class DemoChannelsTests(CorpusChannelChecks):
    def test_text_and_url_produce_the_same_per_claim_outcomes_across_channels(self):
        article = f"<title>Artykuł</title><main><p>{SUPPORTED}</p><p>{UNSUPPORTED}</p></main>".encode()
        dns_result = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]
        chunk = rso_chunk()
        with (
            patch("verify.providers._local_completion", side_effect=complete_fixture),
            patch("verify.retrieval.embed", return_value=[chunk.embedding]),
            patch("verify.fetch.socket.getaddrinfo", return_value=dns_result),
            patch("verify.fetch._request", return_value=(None, extract(article, "text/html", "utf-8", "test"))) as fetch,
        ):
            for mode, material in (("text", f"{SUPPORTED} {UNSUPPORTED}"), ("url", ARTICLE_URL)):
                with self.subTest(mode=mode):
                    result = self.compare_channels(material, mode)
                    self.assertEqual(result["status"], "multiple_claims")
                    self.assertEqual([item["status"] for item in result["claims"]], ["resolved", "abstained"])
                    self.assertEqual(result["claims"][0]["evidence"][0]["chunk_id"], str(chunk.pk))
                    self.assertEqual(result["claims"][1]["evidence"], [])
            self.assertEqual(fetch.call_count, 2)

        self.assertEqual(VerificationRun.objects.filter(channel="web").count(), 2)
        self.assertEqual(VerificationRun.objects.filter(channel="cli").count(), 2)


@skipUnless(find_spec("discord"), "Wymaga dodatku discord.")
@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="", PRAWDOWID_LLM_URL="", RAG_TOP_K="1", DISCORD_BOT_TOKEN="test")
class DiscordDemoFlowTest(TransactionTestCase):
    def test_slash_command_returns_evidence_from_corpus_and_saves_result(self):
        from .discord_bot import VerificationBot

        load_demo_corpus()
        chunk = rso_chunk()
        bot = VerificationBot(10)
        interaction = Mock()
        interaction.user.id = 20
        interaction.filesize_limit = 1024 * 1024
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        command = bot.tree.get_command("sprawdz", guild=bot.guild_scope)
        with (
            patch("verify.providers._local_completion", side_effect=complete_fixture),
            patch("verify.retrieval.embed", return_value=[chunk.embedding]),
        ):
            async_to_sync(command.callback)(bot, interaction, SUPPORTED)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        message = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Potwierdzone", message)
        self.assertIn("MSWiA", message)
        self.assertIn(f"<{RSO_URL}>", message)
        run = VerificationRun.objects.get(channel="discord")
        self.assertEqual(run.outcomes[0]["evidence"][0]["document_id"], chunk.document_id)

        captured = []

        async def capture_export(*, content, attachments):
            self.assertIn("Plik JSON", content)
            attachment, = attachments
            captured.append((attachment.filename, json.load(attachment.fp)))

        interaction.edit_original_response.side_effect = capture_export

        for export_type, filename, detail in (
            ("answer", "prawdowid-odpowiedz.json", "full"), ("history", "prawdowid-historia.json", "summary"),
        ):
            with self.subTest(export_type=export_type):
                interaction.response.defer.reset_mock()
                export = bot.tree.get_command(f"export_{export_type}", guild=bot.guild_scope)
                async_to_sync(export.callback)(bot, interaction)
                interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
                actual_filename, data = captured.pop()
                self.assertEqual(actual_filename, filename)
                self.assertEqual((data["schema_version"], data["export_type"]), (1, export_type))
                [record] = data["runs"]
                self.assertEqual(record["request_id"], str(run.request_id))
                self.assertEqual(record["detail_level"], detail)
                evidence = record["outcomes"][0]["evidence"][0]
                self.assertEqual(evidence["document_id"], chunk.document_id)
                if export_type == "answer":
                    self.assertEqual(evidence["url"], RSO_URL)
                    self.assertIn("darmowa, powszechna usługa", evidence["quote"])

        interaction.edit_original_response.side_effect = None
        export_history = bot.tree.get_command("export_history", guild=bot.guild_scope)
        interaction.filesize_limit = 1
        async_to_sync(export_history.callback)(bot, interaction)
        self.assertEqual(interaction.edit_original_response.await_args.kwargs,
                         {"content": "Plik JSON przekracza limit załącznika Discorda."})

        interaction.user.id = 21
        async_to_sync(export_history.callback)(bot, interaction)
        self.assertEqual(interaction.edit_original_response.await_args.kwargs,
                         {"content": "Brak zapisanych sprawdzeń w tej sesji."})


@skipUnless(os.getenv("PRAWDOWID_LIVE_CHANNEL_TESTS") == "1", "Opcjonalna próba z rzeczywistym modelem.")
@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="")
class LiveDemoChannelsTests(CorpusChannelChecks):
    def test_same_text_evidence_with_configured_model(self):
        result = self.compare_channels(SUPPORTED, "text")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["verdict"], "true")
        self.assertTrue(any(item["url"] == RSO_URL for item in result["evidence"]))

    @skipUnless(os.getenv("PRAWDOWID_DEMO_TEST_URL"), "Opcjonalny publiczny adres do wypróbowania.")
    def test_same_article_evidence_with_configured_model(self):
        result = self.compare_channels(os.environ["PRAWDOWID_DEMO_TEST_URL"], "url")
        self.assertIn(result["status"], ("resolved", "abstained", "multiple_claims"))
        for item in result.get("claims", [result]):
            self.assertIn(item["status"], ("resolved", "abstained"))
