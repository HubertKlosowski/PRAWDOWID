from unittest import TestCase
from unittest.mock import patch

from . import fetch as web


def record(ip):
    return (2, 1, 6, "", (ip, 443))


class FetchTests(TestCase):
    def test_main_content_and_metadata(self):
        fixture = b"<title>News</title><nav>Menu</nav><main><p>One</p><script>ignore</script><p>Two</p></main>"
        self.assertEqual(web.extract(fixture, "text/html", "utf-8", "x"), ("News", "One\nTwo"))

    def test_fragment_and_credentials_and_scheme_rejected(self):
        for url in ["file:///tmp/test", "https://a@host.pl/", "https://gov.pl/#/region"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                web.fetch(url)

    @patch.object(web.socket, "getaddrinfo")
    @patch.object(web, "_request")
    def test_redirect_checked_before_next_connection(self, request, resolve):
        resolve.side_effect = [[record("1.1.1.1")], [record("127.0.0.1")]]
        request.return_value = ("http://internal.invalid/", None)
        with self.assertRaises(ValueError):
            web.fetch("https://example.org/")
        self.assertEqual(request.call_count, 1)

    @patch.object(web.socket, "getaddrinfo", return_value=[record("1.1.1.1"), record("10.0.0.2")])
    @patch.object(web, "_request")
    def test_mixed_dns_rejected(self, request, resolve):
        with self.assertRaises(ValueError):
            web.fetch("https://example.org/")
        request.assert_not_called()

    @patch.object(web.socket, "getaddrinfo", return_value=[record("1.1.1.1")])
    @patch.object(web, "_request", return_value=(None, "User-agent: *\nDisallow: /private"))
    def test_robots_rule_checked_before_article(self, request, resolve):
        with self.assertRaisesRegex(ValueError, "robots"):
            web.fetch("https://example.org/private/post", allowed_hosts={"example.org"})
        self.assertEqual(request.call_count, 1)

    @patch.object(web.socket, "getaddrinfo", return_value=[record("1.1.1.1")])
    @patch.object(web, "_request", side_effect=[(None, ""), ("https://other.org/", None)])
    def test_corpus_redirect_stays_in_allowlist(self, request, resolve):
        with self.assertRaisesRegex(ValueError, "domena"):
            web.fetch("https://example.org/", allowed_hosts={"example.org"})
        self.assertEqual(request.call_count, 2)

    def test_request_size_and_media_limits(self):
        from email.message import Message
        from unittest.mock import MagicMock
        import time

        for mime, body, expected in (
            ("application/pdf", b"%PDF-1.7", "HTML"),
            ("text/plain", b"a" * 65, "rozmiaru"),
        ):
            with self.subTest(mime=mime), patch.object(web, "MAX_BYTES", 64), patch.object(
                web.socket, "socket"
            ), patch.object(web.http.client, "HTTPConnection") as connection:
                response = MagicMock()
                response.status = 200
                response.headers = Message()
                response.headers["Content-Type"] = mime
                response.getheader.side_effect = lambda key, default=None: default
                response.read1.side_effect = [body, b""]
                connection.return_value.getresponse.return_value.__enter__.return_value = response
                with self.assertRaisesRegex(ValueError, expected):
                    web._request("http://example.org/", "example.org", 80,
                                 record("1.1.1.1"), time.monotonic() + 2)

    def test_hidden_elements_and_comments(self):
        fixture = "<main><!--instruction--><p hidden>hidden</p><p>Widoczny tekst</p></main>"
        self.assertEqual(web.extract(fixture.encode(), "text/html", None, "test"),
                         ("test", "Widoczny tekst"))
