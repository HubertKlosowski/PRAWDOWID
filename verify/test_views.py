from unittest.mock import patch

from django.test import SimpleTestCase


class VerificationPageTests(SimpleTestCase):
    def test_form_available(self):
        response = self.client.get("/")
        self.assertContains(response, "Sprawdź informację")
        self.assertNotContains(response, "Czas weryfikacji")

    @patch("verify.views.perf_counter", side_effect=[10, 11.25])
    @patch("verify.views.retrieve", return_value=[])
    @patch("verify.views.fetch", return_value=("https://example.org/", "Tytuł", "Treść materiału"))
    def test_url_uses_extracted_text(self, fetch, retrieve, clock):
        response = self.client.post("/", {"mode": "url", "material": "https://example.org/"})
        retrieve.assert_called_once_with("Treść materiału")
        self.assertContains(response, "Niezweryfikowane")
        self.assertContains(response, "W korpusie nie znaleziono")
        self.assertContains(response, "Czas weryfikacji <strong>1,25 s</strong>")

    @patch("verify.views.retrieve")
    def test_invalid_url_stops_before_retrieval(self, retrieve):
        response = self.client.post("/", {"mode": "url", "material": "file:///example"})
        self.assertTrue(response.context["form"].errors)
        retrieve.assert_not_called()
