import os

from django.contrib.staticfiles.management.commands.runserver import Command as RunserverCommand

from verify.embeddings import load_model
from verify.providers import DEFAULT_LLM_MODEL, load_local_model


class Command(RunserverCommand):
    help = "Ładuje modele do pamięci procesu i uruchamia serwer demonstracyjny."

    def inner_run(self, *args, **options):
        self.stdout.write("Ładowanie modelu embeddingowego…")
        load_model()
        if not os.getenv("PRAWDOWID_LLM_URL"):
            name = os.getenv("PRAWDOWID_LLM_MODEL", DEFAULT_LLM_MODEL)
            self.stdout.write(f"Ładowanie modelu oceniającego {name}…")
            try:
                load_local_model(name)
            except OSError:
                if name != DEFAULT_LLM_MODEL:
                    raise
                from huggingface_hub import snapshot_download

                snapshot_download(name)
                load_local_model(name)
        return super().inner_run(*args, **options)
