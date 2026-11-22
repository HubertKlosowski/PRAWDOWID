import json
from collections import Counter, defaultdict
from statistics import median

from django.core.management.base import BaseCommand
from django.utils import timezone

from verify.models import VerificationRun


class Command(BaseCommand):
    help = "Wyświetla zbiorcze metryki aktywnych sesji bez treści zgłoszeń."

    def handle(self, *args, **options):
        rows = VerificationRun.objects.filter(session__expires_at__gt=timezone.now()).values(
            "status", "elapsed_ms", "stages", "models_used", "outcomes", "estimated_cost", "cost_method",
        )
        statuses, claim_statuses, cost_methods = Counter(), Counter(), Counter()
        stages = defaultdict(Counter)
        durations = []
        models = set()
        known_costs = []
        for row in rows.iterator():
            statuses[row["status"]] += 1
            claim_statuses.update(item["status"] for item in row["outcomes"] if "claim" in item)
            durations.append(row["elapsed_ms"])
            for stage, values in row["stages"].items():
                stages[stage].update(values)
            for role, details in row["models_used"].items():
                models.add((role, details["id"], details["backend"], details.get("revision", "")))
            cost_methods[row["cost_method"]] += 1
            if row["estimated_cost"] is not None:
                known_costs.append(row["estimated_cost"])
        count = len(durations)
        self.stdout.write(json.dumps({
            "runs": count,
            "statuses": statuses,
            "claim_statuses": claim_statuses,
            "abstained_share": statuses["abstained"] / count if count else None,
            "elapsed_ms_median": median(durations) if count else None,
            "elapsed_ms_total": sum(durations),
            "retrieval_errors": stages.get("retrieval", {}).get("errors", 0),
            "stages": stages,
            "models": [
                {"role": role, "id": name, "backend": backend, "revision": revision or None}
                for role, name, backend, revision in sorted(models)
            ],
            "cost": {"methods": cost_methods, "known_total": str(sum(known_costs)) if known_costs else None},
        }, ensure_ascii=False))
