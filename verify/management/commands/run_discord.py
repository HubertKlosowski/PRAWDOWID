import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Uruchamia komendę /sprawdz na serwerze demonstracyjnym Discorda."

    def handle(self, *args, **options):
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
        if not token or not guild_id.isascii() or not guild_id.isdigit() or not 0 < int(guild_id) < 2**64:
            raise CommandError("Ustaw DISCORD_BOT_TOKEN i numeryczny DISCORD_GUILD_ID.")
        try:
            from verify.discord_bot import VerificationBot
        except ModuleNotFoundError as exc:
            if exc.name != "discord":
                raise
            raise CommandError("Uruchom z opcją uv run --extra discord.") from exc

        VerificationBot(int(guild_id)).run(token, log_handler=None)
