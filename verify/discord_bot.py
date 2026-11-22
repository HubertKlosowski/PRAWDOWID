"""Discord Gateway adapter for the demonstration server."""

import asyncio
import logging
from io import BytesIO

import discord
from discord import app_commands
from django.db import DatabaseError, connections

from .audit import active_session_runs, delete_session, discord_session_key
from .history_export import (
    EXPORT_ANSWER_FILENAME, EXPORT_HISTORY_FILENAME, EXPORT_UNAVAILABLE, NO_ANSWER, NO_HISTORY,
    ExportTooLarge, build_export,
)
from .security import enforce_limit, limit_message
from .verification import InvalidMaterial, material_mode, verify

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "needs_clarification": "Doprecyzuj zgłoszenie",
    "out_of_scope": "Poza zakresem weryfikacji",
    "material_unavailable": "Materiał niedostępny",
    "technical_error": "Błąd techniczny",
    "abstained": "Niezweryfikowane",
}


def render_result(result):
    if result.get("claims"):
        lines = ["Wyniki dla poszczególnych tez"]
        if result.get("claims_truncated"):
            lines.append("Pokazujemy najwyżej cztery tezy. Pozostałe sprawdź osobno.")
        for item in result["claims"]:
            lines.extend(_render_claim(item))
        return lines
    return _render_claim(result)


def _render_claim(result):
    if result["status"] == "resolved":
        label = {"true": "Potwierdzone", "false": "Zaprzeczone", "misleading": "Wprowadzające w błąd"}[result["verdict"]]
    else:
        label = STATUS_LABELS[result["status"]]
    lines = []
    if result.get("claim"):
        lines.append(f'Teza: {result["claim"]}')
    if result.get("claim_context"):
        lines.append(f'Kontekst zgłoszenia: {result["claim_context"]}')
    lines.extend((label, result["explanation"]))
    if strength := result.get("evidence_strength"):
        lines.append(f'Siła dowodów: {strength["label"]}\n' + "\n".join(strength["factors"]))
    if scores := result.get("class_scores"):
        lines.append("Nieskalibrowane szacunki modelu — " + ", ".join(
            f"{name.lower()} {score:.2f}" for name, score in scores.items()
        ))
    lines.extend(result.get("limitations", []))
    if result.get("google_fact_check_unavailable"):
        lines.append("Wyszukiwanie w Google Fact Check jest niedostępne lub niepełne.")
    for item in result.get("evidence", []):
        publisher = item.get("publisher") or "Wydawca nieustalony"
        publication = item.get("published_at") or "nieznana"
        dates = f"Data publikacji: {publication}."
        if retrieved_at := item.get("retrieved_at"):
            dates += f" Pobrano: {retrieved_at}."
        if item.get("valid_from") or item.get("valid_to"):
            start = item.get("valid_from")
            end = item.get("valid_to")
            bounds = " ".join(part for part in (f"od {start}" if start else "", f"do {end}" if end else "") if part)
            dates += f" Okres ważności {bounds}."
        lines.append(f'{item["title"]} · {publisher}\n{dates}\n„{item["quote"]}”\n<{item["url"]}>')
    for item in result.get("fact_checks", []):
        lines.append(f'Weryfikacja wydawcy — {item["review_title"] or item["publisher"]}\n<{item["source_url"]}>')
    return lines


def check_material(material, session_key):
    try:
        retry_after = enforce_limit("discord", session_key)
        if retry_after is not None:
            return [limit_message(retry_after)]
        result = verify(material, mode=material_mode(material), session_key=session_key, channel="discord")
        lines = render_result(result)
        if result.get("audit_unavailable"):
            lines.append("Zapis przebiegu weryfikacji jest niedostępny.")
        return lines
    except InvalidMaterial as exc:
        return [str(exc)]
    except Exception as exc:
        logger.error("Discord verification failed (%s)", type(exc).__name__)
        return ["Błąd techniczny. Spróbuj ponownie później."]
    finally:
        connections.close_all()


def delete_discord_history(session_key):
    try:
        delete_session(session_key, "discord")
    finally:
        connections.close_all()


def export_discord_data(session_key, latest_only, max_bytes):
    try:
        runs = active_session_runs(session_key, "discord")
        return build_export(runs, latest_only=latest_only, max_bytes=max_bytes)
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
        self.tree.command(name="usun_sesje", description="Usuń historię weryfikacji na tym serwerze.",
                          guild=self.guild_scope)(self.delete_history)
        self.tree.command(name="export_answer", description="Pobierz ostatnią zapisaną odpowiedź jako JSON.",
                          guild=self.guild_scope)(self.export_answer)
        self.tree.command(name="export_history", description="Pobierz historię tej sesji jako JSON.",
                          guild=self.guild_scope)(self.export_history)

    async def setup_hook(self):
        await self.tree.sync(guild=self.guild_scope)

    @app_commands.describe(material="Twierdzenie lub pełny adres URL artykułu")
    async def check(self, interaction: discord.Interaction, material: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.task and not self.task.done():
            await interaction.edit_original_response(content="Trwa inne sprawdzenie. Spróbuj ponownie za chwilę.")
            return
        key = discord_session_key(self.guild_scope.id, interaction.user.id)
        self.task = asyncio.create_task(asyncio.to_thread(check_material, material, key))
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
            content += "\n\nPełny wynik ze źródłami jest w załączniku."
        await interaction.edit_original_response(content=content, attachments=attachments,
                                                 allowed_mentions=discord.AllowedMentions.none())

    async def delete_history(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = discord_session_key(self.guild_scope.id, interaction.user.id)
        try:
            await asyncio.to_thread(delete_discord_history, key)
        except DatabaseError:
            message = "Nie udało się usunąć historii. Spróbuj ponownie później."
        else:
            message = "Historia sesji w PRAWDOWID została usunięta."
        await interaction.edit_original_response(content=message)

    async def export_answer(self, interaction: discord.Interaction):
        await self._send_export(interaction, latest_only=True)

    async def export_history(self, interaction: discord.Interaction):
        await self._send_export(interaction, latest_only=False)

    async def _send_export(self, interaction, *, latest_only):
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = discord_session_key(self.guild_scope.id, interaction.user.id)
        # The interaction's file size is available in discord.py 2.6 and newer.
        max_bytes = getattr(interaction, "filesize_limit", None)
        if max_bytes is None and interaction.guild is not None:
            max_bytes = interaction.guild.filesize_limit
        try:
            file = await asyncio.to_thread(export_discord_data, key, latest_only, max_bytes)
        except ExportTooLarge:
            message = "Plik JSON przekracza limit załącznika Discorda."
        except (DatabaseError, OSError):
            message = EXPORT_UNAVAILABLE
        else:
            if file is not None:
                filename = EXPORT_ANSWER_FILENAME if latest_only else EXPORT_HISTORY_FILENAME
                with file:
                    attachment = discord.File(file, filename=filename)
                    try:
                        await interaction.edit_original_response(content="Plik JSON jest w załączniku.",
                                                                 attachments=[attachment])
                    finally:
                        attachment.close()
                return
            message = NO_ANSWER if latest_only else NO_HISTORY
        await interaction.edit_original_response(content=message)
