from importlib.util import find_spec
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, MagicMock, patch


@skipUnless(find_spec("discord"), "Wymaga dodatku discord")
class DiscordTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from .discord_bot import VerificationBot

        self.bot = VerificationBot(12345)
        self.interaction = MagicMock()
        self.interaction.response.defer = AsyncMock()
        self.interaction.edit_original_response = AsyncMock()

    async def submit(self, material, result):
        command = self.bot.tree.get_command("sprawdz", guild=self.bot.guild_scope)
        async def inline(function, *args):
            return function(*args)

        with patch("verify.discord_bot.asyncio.to_thread", inline), patch(
            "verify.discord_bot.verify", return_value=result
        ) as verify:
            await command.callback(self.bot, self.interaction, material)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        return verify, self.interaction.edit_original_response.await_args.kwargs

    async def test_url_with_source(self):
        url = "https://example.org/tekst"
        verify, reply = await self.submit(url, {
            "status": "resolved", "verdict": "true", "explanation": "Zgodne z komunikatem.",
            "evidence": [{"title": "Komunikat", "quote": "Fragment", "url": "https://example.org/dowod"}],
        })
        verify.assert_called_once_with(url, mode="url")
        self.assertIn("Potwierdzone", reply["content"])
        self.assertIn("<https://example.org/dowod>", reply["content"])

    async def test_long_abstention(self):
        verify, reply = await self.submit("Twierdzenie", {
            "status": "abstained", "explanation": "Brak danych. " * 200,
        })
        verify.assert_called_once_with("Twierdzenie", mode="text")
        self.assertIn("Niezweryfikowane", reply["content"])
        self.assertLessEqual(len(reply["content"]), 2000)
        self.assertEqual(reply["attachments"][0].filename, "weryfikacja.txt")
