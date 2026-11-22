import json

from django.core.management.base import BaseCommand

from verify.audit import prune_expired


class Command(BaseCommand):
    help = "Usuwa wygasłe sesje weryfikacji i ich historię."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(prune_expired()))
