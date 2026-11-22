from importlib.util import find_spec
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, MagicMock, patch


@skipUnless(find_spec("discord"), "Wymaga dodatku discord")
class DiscordTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from .discord_bot import VerificationBot

        self.bot = VerificationBot(12345)
        self.interaction = MagicMock()
        self.interaction.user.id = 67890
        self.interaction.response.defer = AsyncMock()
        self.interaction.edit_original_response = AsyncMock()

    async def submit(self, material, result):
        command = self.bot.tree.get_command("sprawdz", guild=self.bot.guild_scope)
        async def inline(function, *args):
            return function(*args)

        with (
            patch("verify.discord_bot.asyncio.to_thread", inline),
            patch("verify.discord_bot.enforce_limit", return_value=None),
            patch("verify.discord_bot.verify", return_value=result) as verify,
        ):
            await command.callback(self.bot, self.interaction, material)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        return verify, self.interaction.edit_original_response.await_args.kwargs

    async def test_url_with_source(self):
        url = "https://example.org/tekst"
        verify, reply = await self.submit(url, {
            "status": "resolved", "verdict": "true", "explanation": "Zgodne z komunikatem.",
            "evidence": [{"title": "Komunikat", "quote": "Fragment", "url": "https://example.org/dowod",
                          "publisher": "Testowy urząd", "published_at": "2026-11-19",
                          "retrieved_at": "2026-11-21T10:00:00+00:00"}],
        })
        from .audit import discord_session_key
        verify.assert_called_once_with(url, mode="url", channel="discord",
                                       session_key=discord_session_key(12345, 67890))
        self.assertIn("Potwierdzone", reply["content"])
        self.assertIn("<https://example.org/dowod>", reply["content"])
        self.assertIn("Testowy urząd", reply["content"])
        self.assertIn("Data publikacji: 2026-11-19.", reply["content"])
        self.assertIn("Pobrano: 2026-11-21", reply["content"])

    async def test_long_abstention(self):
        verify, reply = await self.submit("Twierdzenie", {
            "status": "abstained", "explanation": "Brak danych. " * 200,
        })
        from .audit import discord_session_key
        verify.assert_called_once_with("Twierdzenie", mode="text", channel="discord",
                                       session_key=discord_session_key(12345, 67890))
        self.assertIn("Niezweryfikowane", reply["content"])
        self.assertLessEqual(len(reply["content"]), 2000)
        self.assertEqual(reply["attachments"][0].filename, "weryfikacja.txt")

    async def test_multiple_claims_keep_verdicts_and_links(self):
        _, reply = await self.submit("Dwie tezy.", {
            "status": "multiple_claims", "claims": [
                {"claim": "Pierwsza teza.", "status": "resolved", "verdict": "true",
                 "explanation": "Zgodna ze źródłem.",
                 "evidence_strength": {"label": "Umiarkowana", "factors": ["Dokumenty: 1."]},
                 "evidence": [{"title": "Pierwsze źródło", "quote": "Cytat A", "url": "https://example.org/a"}]},
                {"claim": "Druga teza.", "status": "resolved", "verdict": "misleading",
                 "explanation": "Brak ważnego kontekstu.",
                 "evidence": [{"title": "Drugie źródło", "quote": "Cytat B", "url": "https://example.org/b"}]},
            ],
        })
        content = reply["content"]
        expected = ("Pierwsza teza.", "Potwierdzone", "Siła dowodów: Umiarkowana",
                    "Dokumenty: 1.", "https://example.org/a",
                    "Druga teza.", "Wprowadzające w błąd", "https://example.org/b")
        self.assertEqual([content.index(item) for item in expected],
                         sorted(content.index(item) for item in expected))

    async def test_delete_command_uses_only_the_invoking_users_session(self):
        from .audit import discord_session_key

        command = self.bot.tree.get_command("usun_sesje", guild=self.bot.guild_scope)
        with patch("verify.discord_bot.asyncio.to_thread", new_callable=AsyncMock) as thread:
            await command.callback(self.bot, self.interaction)
        self.assertEqual(thread.await_args.args[1:], (discord_session_key(12345, 67890),))
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertIn("została usunięta", self.interaction.edit_original_response.await_args.kwargs["content"])

    async def test_rate_limit_stops_verification_and_replies_privately(self):
        command = self.bot.tree.get_command("sprawdz", guild=self.bot.guild_scope)
        with (
            patch("verify.discord_bot.enforce_limit", return_value=45),
            patch("verify.discord_bot.verify") as verify,
        ):
            await command.callback(self.bot, self.interaction, "Twierdzenie")
        verify.assert_not_called()
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertIn("45 s", self.interaction.edit_original_response.await_args.kwargs["content"])
