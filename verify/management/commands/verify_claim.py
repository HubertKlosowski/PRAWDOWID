import json

from django.core.management.base import BaseCommand, CommandError

from verify.verification import InvalidMaterial, verify


class Command(BaseCommand):
    help = "Weryfikuje tekst lub artykuł i zwraca wynik jako JSON."

    def add_arguments(self, parser):
        inputs = parser.add_mutually_exclusive_group(required=True)
        inputs.add_argument("--text", help="Twierdzenie do sprawdzenia.")
        inputs.add_argument("--url", help="Adres artykułu do sprawdzenia.")

    def handle(self, *args, text=None, url=None, **options):
        try:
            result = verify(url, mode="url") if url is not None else verify(text)
        except InvalidMaterial as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
