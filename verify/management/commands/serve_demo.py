import os
from urllib.parse import urlsplit

from django.contrib.staticfiles.management.commands.runserver import Command as RunserverCommand
from django.core.management.base import CommandError

from verify.embeddings import load_model
from verify.providers import DEFAULT_LLM_MODEL, load_local_model


class Command(RunserverCommand):
    help = "Ładuje modele do pamięci procesu i uruchamia serwer demonstracyjny."

    def run(self, **options):
        # Keep one warmed process and surface startup errors in the command runner.
        options["use_reloader"] = False
        return super().run(**options)

    def inner_run(self, *args, **options):
        self.preload_models()
        return super().inner_run(*args, **options)

    def preload_models(self):
        self.stdout.write("Ładowanie modelu embeddingowego…")
        try:
            load_model()
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            raise CommandError(
                "Nie udało się załadować modelu embeddingowego. Sprawdź wagi Qwen3-Embedding-0.6B, "
                "ustawienia QWEN_EMBEDDING_PATH i QWEN_DEVICE oraz dostępną pamięć."
            ) from exc

        endpoint = os.getenv("PRAWDOWID_LLM_URL")
        if endpoint:
            try:
                parsed = urlsplit(endpoint)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise ValueError
            except ValueError as exc:
                raise CommandError("PRAWDOWID_LLM_URL musi być pełnym adresem HTTP lub HTTPS.") from exc
            return

        name = os.getenv("PRAWDOWID_LLM_MODEL", DEFAULT_LLM_MODEL)
        self.stdout.write("Ładowanie lokalnego modelu oceniającego…")
        try:
            try:
                load_local_model(name)
            except OSError:
                if name != DEFAULT_LLM_MODEL:
                    raise
                from huggingface_hub import snapshot_download

                snapshot_download(name)
                load_local_model(name)
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            raise CommandError(
                "Nie udało się załadować lokalnego modelu oceniającego. Sprawdź PRAWDOWID_LLM_MODEL, "
                "dostępność wag oraz pamięć urządzenia wskazanego przez PRAWDOWID_LLM_DEVICE."
            ) from exc
