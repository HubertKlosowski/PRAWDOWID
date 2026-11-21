"""Discord Gateway adapter for the demonstration server."""

import asyncio
import logging
from io import BytesIO

import discord
from discord import app_commands
from django.db import connections

from .verification import InvalidMaterial, material_mode, verify

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "needs_clarification": "Doprecyzuj zgłoszenie",
    "out_of_scope": "Poza zakresem weryfikacji",
    "technical_error": "Błąd techniczny",
    "abstained": "Niezweryfikowane",
}


def render_result(result):
    if result["status"] == "resolved":
        label = {"true": "Potwierdzone", "false": "Zaprzeczone"}[result["verdict"]]
    else:
        label = STATUS_LABELS[result["status"]]
    lines = [label, result["explanation"]]
    if scores := result.get("class_scores"):
        lines.append("Nieskalibrowane szacunki modelu — " + ", ".join(
            f"{name.lower()} {score:.2f}" for name, score in scores.items()
        ))
    lines.extend(result.get("limitations", []))
    if result.get("google_fact_check_unavailable"):
        lines.append("Wyszukiwanie w Google Fact Check jest niedostępne lub niepełne.")
    for item in result.get("evidence", []):
        lines.append(f'{item["title"]} — „{item["quote"]}”\n<{item["url"]}>')
    for item in result.get("fact_checks", []):
        lines.append(f'Weryfikacja wydawcy — {item["review_title"] or item["publisher"]}\n<{item["source_url"]}>')
    return lines


def check_material(material):
    try:
        return render_result(verify(material, mode=material_mode(material)))
    except InvalidMaterial as exc:
        return [str(exc)]
    except Exception as exc:
        logger.error("Discord verification failed (%s)", type(exc).__name__)
        return ["Błąd techniczny. Spróbuj ponownie później."]
    finally:
        connections.close_all()


class VerificationBot(discord.Client):
    def __init__(self, guild_id):
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.guild_scope = discord.Object(id=guild_id)
        self.tree = app_commands.CommandTree(self)
        self.task = None
        self.tree.command(name="sprawdz", description="Sprawdź twierdzenie lub adres artykułu.",
                          guild=self.guild_scope)(self.check)

    async def setup_hook(self):
        await self.tree.sync(guild=self.guild_scope)

    @app_commands.describe(material="Twierdzenie lub pełny adres URL artykułu")
    async def check(self, interaction: discord.Interaction, material: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.task and not self.task.done():
            await interaction.edit_original_response(content="Trwa inne sprawdzenie. Spróbuj ponownie za chwilę.")
            return
        self.task = asyncio.create_task(asyncio.to_thread(check_material, material))
        try:
            lines = await asyncio.wait_for(asyncio.shield(self.task), timeout=14 * 60)
        except TimeoutError:
            await interaction.edit_original_response(content="Analiza przekroczyła czas odpowiedzi Discorda.")
            return
        # Keep complete citations together. Attach the full report if it exceeds the message limit.
        report = "\n\n".join(lines)
        attachments = []
        content = report
        if len(content.encode("utf-16-le")) // 2 > 2000:
            attachments.append(discord.File(BytesIO(report.encode()), filename="weryfikacja.txt"))
            content = "\n\n".join(lines[:2])[:450]
            for line in lines[2:]:
                if "\n<" in line and len((content + "\n\n" + line).encode("utf-16-le")) // 2 <= 2000:
                    content += "\n\n" + line
        await interaction.edit_original_response(content=content, attachments=attachments,
                                                 allowed_mentions=discord.AllowedMentions.none())
