import json
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import audit, google_fact_check
from .analysis import unresolved
from .claims import CLAIMS_PROMPT
from .models import VerificationRun, VerificationSession
from .privacy import mask_text
from .providers import request_completion
from .verification import verify

MATERIAL = "Kontakt: test@example.org, +48 501 234 567, PESEL 01234567890."
MASKED = "Kontakt: [EMAIL], [TELEFON], PESEL [IDENTYFIKATOR]."


class MaskingTests(SimpleTestCase):
    def test_mask_preserves_dates_and_removes_identifiers_and_url_details(self):
        text = MATERIAL + " Dnia 2026-11-22. https://example.org/reset/secret?token=secret#fragment"
        self.assertEqual(mask_text(text), MASKED + " Dnia 2026-11-22. https://example.org")
        self.assertEqual(mask_text("(22) 123 45 67, NIP 123-456-78-90, dowodu ABC123456"),
                         "[TELEFON], NIP [IDENTYFIKATOR], dowodu [IDENTYFIKATOR]")
        self.assertEqual(mask_text("https://[2001:db8::1]/private?token=secret"), "https://[2001:db8::1]")

    @patch.dict("os.environ", PRAWDOWID_LLM_URL="https://provider.example.org/chat")
    def test_http_provider_only_receives_masked_nested_material(self):
        with patch("verify.providers._remote_completion", return_value={}) as remote:
            request_completion("Instrukcja", {"material": MATERIAL, "candidates": [
                {"id": "123456789", "text": MATERIAL, "title": "test@example.org"},
            ]})
        messages = remote.call_args.args[2]
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["material"], MASKED)
        self.assertEqual(payload["candidates"], [{"id": "123456789", "text": MASKED, "title": "[EMAIL]"}])

    def test_google_receives_masked_query(self):
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b"{}")))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch("verify.google_fact_check.build_opener", return_value=opener):
            google_fact_check.search(MATERIAL, api_key="test-key")
        request = opener.open.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)["query"], [MASKED])

    @patch.dict("os.environ", PRAWDOWID_LLM_URL="https://provider.example.org/chat", GOOGLE_FACTCHECK_API_KEY="")
    def test_verbatim_extraction_accepts_masked_material_with_remote_model(self):
        def complete(prompt, data):
            self.assertEqual(prompt, CLAIMS_PROMPT)
            self.assertEqual(data["material"], MASKED)
            return {"claims": [{"text": MASKED, "context": None}], "truncated": False}

        result = verify(MATERIAL, complete=complete, search=lambda *_: [])
        self.assertEqual(result["claim"], MASKED)
        self.assertEqual(result["status"], "abstained")


@override_settings(PRAWDOWID_RETENTION_DAYS=7)
@patch.dict("os.environ", GOOGLE_FACTCHECK_API_KEY="", PRAWDOWID_LLM_URL="", PRAWDOWID_LLM_MODEL="demo/model")
class VerificationHistoryTests(TestCase):
    def test_exports_latest_answer_and_session_history_without_cross_channel_data(self):
        key = audit.new_session_key()
        self.client.cookies[audit.COOKIE_NAME] = key
        legacy = audit.open_trace(key, "web", "text")
        audit.save_run(legacy, "Starsze pytanie", unresolved("abstained"))
        VerificationRun.objects.filter(pk=legacy.request_id).update(answer_snapshot=None)

        legacy_response = self.client.get("/sesja/odpowiedz.json")
        legacy_run = json.loads(legacy_response.getvalue())["runs"][0]
        self.assertEqual(legacy_run["detail_level"], "summary")
        self.assertEqual(legacy_run["request_id"], str(legacy.request_id))

        answer = audit.open_trace(key, "web", "url")
        supported = {
            "status": "resolved", "verdict": "true", "claim": MATERIAL,
            "class_scores": {"POTWIERDZONE": 1.0}, "score_type": "model_estimate_not_calibrated",
            "explanation": "Zgodne ze źródłem. " + MATERIAL,
            "evidence_strength": {"level": "moderate", "factors": ["Jedno źródło."]},
            "evidence": [{"id": "11.1", "document_id": 4, "chunk_id": "11", "relation": "supports",
                          "title": "Komunikat", "publisher": "Testowy urząd", "quote": MATERIAL,
                          "published_at": "2026-11-20", "url": "https://example.org/dowod?token=secret#fragment"}],
            "fact_checks": [{"id": "fc-1", "cited": True, "claimant": "Imię i nazwisko",
                             "source_url": "https://example.org/weryfikacja"}],
            "internal_data": "secret",
        }
        audit.save_run(answer, "https://example.org/private?token=secret", {
            "status": "multiple_claims", "claims_truncated": True,
            "claims": [supported, unresolved("technical_error", reason_code="retrieval_failed")],
        })
        rejected = audit.open_trace(key, "web", "url")
        audit.save_run(rejected, "https://example.org/private?token=secret", {"status": "rejected"})
        discord = audit.open_trace(key, "discord", "text")
        audit.save_run(discord, "Prywatna teza Discord", unresolved("abstained"))
        other = audit.open_trace(audit.new_session_key(), "web", "text")
        audit.save_run(other, "Prywatna teza innej osoby", unresolved("abstained"))

        self.assertContains(self.client.get("/"), "Pobierz ostatnią odpowiedź (JSON)")
        self.assertContains(self.client.get("/"), "Pobierz historię tej sesji (JSON)")
        response = self.client.get("/sesja/odpowiedz.json", {"session": other.request_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="prawdowid-odpowiedz.json"')
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertIn("no-store", response.headers["Cache-Control"])
        raw = response.getvalue()
        for sensitive in (b"test@example.org", b"secret", b"claimant", key.encode()):
            self.assertNotIn(sensitive, raw)
        exported = json.loads(raw)
        self.assertEqual((exported["schema_version"], exported["export_type"]), (1, "answer"))
        run, = exported["runs"]
        self.assertEqual(run["request_id"], str(answer.request_id))
        self.assertEqual(run["question"], "https://example.org")
        self.assertEqual(run["detail_level"], "full")
        self.assertTrue(run["claims_truncated"])
        first, second = run["outcomes"]
        self.assertEqual(first["claim"], MASKED)
        self.assertEqual(first["explanation"], "Zgodne ze źródłem. " + MASKED)
        self.assertEqual(first["score_type"], "model_estimate_not_calibrated")
        self.assertEqual(first["evidence"][0]["quote"], MASKED)
        self.assertEqual(first["evidence"][0]["url"], "https://example.org/dowod")
        self.assertEqual(first["fact_checks"][0]["source_url"], "https://example.org/weryfikacja")
        self.assertEqual((second["status"], second["verdict"]), ("technical_error", None))

        response = self.client.get("/sesja/historia.json")
        exported = json.loads(response.getvalue())
        self.assertEqual((exported["schema_version"], exported["export_type"]), (1, "history"))
        self.assertEqual([item["request_id"] for item in exported["runs"]],
                         [str(trace.request_id) for trace in (rejected, answer, legacy)])
        self.assertEqual(exported["runs"][0]["question"], "")
        summary = exported["runs"][1]
        self.assertEqual(summary["detail_level"], "summary")
        self.assertEqual(summary["outcomes"][0]["evidence"],
                         [{"document_id": 4, "chunk_id": "11", "relation": "supports"}])
        self.assertNotIn("explanation", summary["outcomes"][0])

    def test_exports_respect_expiry_and_do_not_create_a_session(self):
        self.assertContains(self.client.get("/sesja/odpowiedz.json"), "Brak zapisanej odpowiedzi", status_code=404)
        self.assertFalse(VerificationSession.objects.exists())
        key = audit.new_session_key()
        self.client.cookies[audit.COOKIE_NAME] = key
        trace = audit.open_trace(key, "web", "text")
        audit.save_run(trace, "Pytanie", unresolved("abstained"))
        VerificationSession.objects.filter(pk=trace.session_id).update(expires_at=timezone.now())
        self.assertContains(self.client.get("/sesja/historia.json"), "Brak zapisanych sprawdzeń", status_code=404)
        self.assertTrue(VerificationRun.objects.filter(pk=trace.request_id).exists())
        audit.delete_session(key, "web")
        self.assertEqual(self.client.get("/sesja/odpowiedz.json").status_code, 404)

    def test_web_records_masked_request_and_deletes_only_its_own_history(self):
        completion = json.dumps({"claims": [{"text": MATERIAL, "context": None}], "truncated": False})
        with (
            patch("verify.providers._local_completion", return_value=completion),
            patch("verify.verification.search_corpus", return_value=[]),
        ):
            response = self.client.post("/", {"material": MATERIAL})
            other = Client()
            other.post("/", {"material": MATERIAL})

        run = VerificationRun.objects.get(request_id=response.context["result"]["request_id"])
        cookie = response.cookies[audit.COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(int(cookie["max-age"]), 7 * 86400)
        self.assertNotEqual(run.session.key_hash, cookie.value)
        self.assertEqual(run.question, MASKED)
        self.assertEqual(run.outcomes[0]["claim"], MASKED)
        self.assertEqual(run.status, "abstained")
        self.assertEqual(run.models_used["llm"], {"id": "demo/model", "backend": "local"})
        self.assertEqual(run.stages["retrieval"]["calls"], 1)
        self.assertEqual(run.stages["model"]["calls"], 1)
        self.assertEqual(run.cost_method, "unknown")
        self.assertIsNone(run.estimated_cost)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertContains(response, "Usuń historię tej sesji")

        self.assertEqual(self.client.get("/sesja/usun/").status_code, 405)
        protected_client = Client(enforce_csrf_checks=True)
        protected_client.cookies[audit.COOKIE_NAME] = cookie.value
        self.assertEqual(protected_client.post("/sesja/usun/").status_code, 403)
        deleted = self.client.post("/sesja/usun/", follow=True)
        self.assertContains(deleted, "Historia sesji została usunięta.")
        self.assertFalse(VerificationRun.objects.filter(pk=run.pk).exists())
        self.assertEqual(VerificationRun.objects.count(), 1)
        self.assertEqual(VerificationSession.objects.count(), 1)

    def test_cli_records_failure_and_supports_metrics_and_deletion(self):
        output = StringIO()
        with (
            patch("verify.providers._local_completion",
                  return_value='{"claims":[{"text":"Teza","context":null}],"truncated":false}'),
            patch("verify.verification.search_corpus", side_effect=RuntimeError(MATERIAL)),
            self.assertLogs("verify.verification", level="WARNING") as logs,
        ):
            call_command("verify_claim", "--text", "Teza", stdout=output)
        result = json.loads(output.getvalue())
        run = VerificationRun.objects.get(request_id=result["request_id"])
        self.assertNotIn(MATERIAL, "\n".join(logs.output))
        self.assertEqual(run.outcomes[0]["reason_code"], "retrieval_failed")
        self.assertEqual(run.stages["retrieval"]["errors"], 1)

        output = StringIO()
        call_command("verification_metrics", stdout=output)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["statuses"], {"technical_error": 1})
        self.assertEqual(summary["retrieval_errors"], 1)
        self.assertEqual(summary["cost"], {"methods": {"unknown": 1}, "known_total": None})
        self.assertNotIn(str(run.request_id), output.getvalue())
        call_command("verify_claim", "--delete-session", "--session", result["session_key"], stdout=StringIO())
        self.assertFalse(VerificationRun.objects.exists())

    def test_retention_prunes_runs_and_prevents_a_late_write_to_deleted_session(self):
        key = audit.new_session_key()
        trace = audit.open_trace(key, "cli", "text")
        self.assertTrue(audit.save_run(trace, MATERIAL, unresolved("abstained")))
        VerificationSession.objects.filter(pk=trace.session_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        output = StringIO()
        call_command("prune_verification_history", stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {"sessions": 1, "runs": 1})

        new_trace = audit.open_trace(key, "cli", "text")
        self.assertNotEqual(new_trace.session_id, trace.session_id)
        self.assertFalse(audit.save_run(trace, MATERIAL, unresolved("abstained")))
        self.assertTrue(audit.save_run(new_trace, MATERIAL, unresolved("abstained")))
        audit.delete_session(key, "web")
        self.assertEqual(VerificationRun.objects.count(), 1)
        audit.delete_session(key, "cli")
        self.assertFalse(audit.save_run(new_trace, MATERIAL, unresolved("abstained")))
        self.assertFalse(VerificationRun.objects.exists())

    def test_audit_keeps_only_reference_ids_and_url_origin(self):
        trace = audit.open_trace(audit.new_session_key(), "cli", "url")
        result = {
            "status": "resolved", "verdict": "true",
            "evidence": [{"document_id": 4, "chunk_id": "11", "relation": "supports",
                          "quote": MATERIAL, "url": "https://example.org/a?token=secret"}],
            "fact_checks": [{"source_url": "https://example.org/a?token=secret", "cited": True}],
        }
        audit.save_run(trace, "https://example.org/private/abc?token=secret", result)
        run = VerificationRun.objects.get(pk=trace.request_id)
        self.assertEqual(run.question, "https://example.org")
        self.assertEqual(run.outcomes[0]["evidence"], [{"document_id": 4, "chunk_id": "11", "relation": "supports"}])
        self.assertEqual(run.outcomes[0]["fact_checks"][0]["origin"], "https://example.org")
        self.assertNotIn("secret", json.dumps(run.outcomes))

    def test_active_demonstrator_cleans_expired_history_when_cleanup_is_due(self):
        old = audit.open_trace(audit.new_session_key(), "web", "text")
        audit.save_run(old, MATERIAL, unresolved("abstained"))
        VerificationSession.objects.filter(pk=old.session_id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with patch("verify.audit._next_prune", 0):
            current = audit.open_trace(audit.new_session_key(), "web", "text")
        self.assertFalse(VerificationRun.objects.filter(pk=old.request_id).exists())
        self.assertTrue(VerificationSession.objects.filter(pk=current.session_id).exists())
