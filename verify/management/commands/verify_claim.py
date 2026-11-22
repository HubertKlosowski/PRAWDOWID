import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from verify.audit import delete_session, new_session_key, valid_session_key
from verify.verification import InvalidMaterial, verify


class Command(BaseCommand):
    help = "Weryfikuje tekst lub artykuł i zwraca wynik jako JSON."

    def add_arguments(self, parser):
        inputs = parser.add_mutually_exclusive_group(required=True)
        inputs.add_argument("--text", help="Twierdzenie do sprawdzenia.")
        inputs.add_argument("--url", help="Adres artykułu do sprawdzenia.")
        inputs.add_argument("--delete-session", action="store_true", help="Usuń historię wskazanej sesji CLI.")
        parser.add_argument("--session", help="Klucz sesji CLI. Możesz też użyć PRAWDOWID_DEMO_SESSION.")

    def handle(self, *args, text=None, url=None, delete_session=False, session=None, **options):
        key = session if session is not None else os.getenv("PRAWDOWID_DEMO_SESSION")
        if key is not None and not valid_session_key(key):
            raise CommandError("Niepoprawny klucz sesji CLI.")
        if delete_session:
            if key is None:
                raise CommandError("Wskaż klucz sesji przez PRAWDOWID_DEMO_SESSION lub --session.")
            self.remove_history(key)
            return

        key = (key or new_session_key()) if settings.PRAWDOWID_AUDIT_ENABLED else None
        try:
            if url is not None:
                result = verify(url, mode="url", session_key=key, channel="cli")
            else:
                result = verify(text, session_key=key, channel="cli")
        except InvalidMaterial as exc:
            # If a key was auto-generated and never disclosed, don't leave rejected history orphaned.
            if key and not (session or os.getenv("PRAWDOWID_DEMO_SESSION")):
                self.remove_history(key, announce=False)
            raise CommandError(str(exc)) from exc
        if key is not None:
            result["session_key"] = key
        self.stdout.write(json.dumps(result, ensure_ascii=False))

    def remove_history(self, key, *, announce=True):
        try:
            delete_session(key, "cli")
        except DatabaseError:
            if announce:
                raise CommandError("Nie udało się usunąć historii sesji.") from None
        if announce:
            self.stdout.write(json.dumps({"status": "session_deleted"}))
