from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .audit import COOKIE_NAME, new_session_key, prune_expired
from .models import RateLimitBucket, SecurityEvent, VerificationRun
from .security import enforce_limit


class SecurityTests(TransactionTestCase):
    def test_www_returns_retry_after_and_new_cookie_cannot_bypass_ip_limit(self):
        with patch("verify.views.verify", return_value=None) as verify:
            for _ in range(5):
                response = self.client.post("/", {"material": "Twierdzenie"}, REMOTE_ADDR="192.0.2.10")
                self.assertEqual(response.status_code, 200)
            old_cookie = self.client.cookies[COOKIE_NAME].value
            other_browser = Client()
            response = other_browser.post("/", {"material": "Twierdzenie"}, REMOTE_ADDR="192.0.2.10",
                                          HTTP_X_FORWARDED_FOR="192.0.2.99")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertContains(response, "Limit sprawdzeń", status_code=429)
        self.assertEqual(verify.call_count, 5)
        self.assertNotEqual(other_browser.cookies[COOKIE_NAME].value, old_cookie)
        self.assertEqual(RateLimitBucket.objects.count(), 2)
        event = SecurityEvent.objects.get()
        self.assertEqual((event.channel, event.code), ("web", "rate_limit"))
        self.assertFalse(RateLimitBucket.objects.filter(key__contains="192.0.2.10").exists())

    def test_concurrent_requests_cannot_exceed_five(self):
        barrier = Barrier(8)
        key = new_session_key()

        def reserve():
            try:
                barrier.wait(timeout=10)
                return enforce_limit("discord", key)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as threads:
            results = list(threads.map(lambda _: reserve(), range(8)))
        self.assertEqual(results.count(None), 5)
        self.assertEqual(sum(value is not None for value in results), 3)
        self.assertEqual(SecurityEvent.objects.filter(code="rate_limit").count(), 1)

    def test_sliding_window_reopens_after_ten_minutes(self):
        key = new_session_key()
        now = timezone.now()
        with patch("verify.security.timezone.now", return_value=now):
            for _ in range(5):
                self.assertIsNone(enforce_limit("discord", key))
        with patch("verify.security.timezone.now", return_value=now + timedelta(seconds=599)):
            self.assertEqual(enforce_limit("discord", key), 1)
        with patch("verify.security.timezone.now", return_value=now + timedelta(seconds=600)):
            self.assertIsNone(enforce_limit("discord", key))

    def test_blocked_url_is_linked_to_audit_without_submitted_url(self):
        response = self.client.post("/", {"material": "http://127.0.0.1/private?token=fixture"})
        self.assertContains(response, "Dozwolone są wyłącznie publiczne adresy serwerów.")
        event = SecurityEvent.objects.get(code="blocked_url_address")
        self.assertTrue(VerificationRun.objects.filter(request_id=event.request_id, question="").exists())

    def test_invalid_model_response_creates_event_and_respects_retention(self):
        with patch("verify.providers._local_completion", return_value="Niepoprawna odpowiedź"):
            response = self.client.post("/", {"material": "Twierdzenie."})
        self.assertEqual(response.context["result"]["status"], "technical_error")
        event = SecurityEvent.objects.get(code="invalid_model_response")
        self.assertEqual(str(event.request_id), response.context["result"]["request_id"])
        SecurityEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(days=8))
        prune_expired()
        self.assertFalse(SecurityEvent.objects.filter(pk=event.pk).exists())
