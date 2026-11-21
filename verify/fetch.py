import codecs
import http.client
import ipaddress
import re
import socket
import ssl
import time
from threading import Timer
from contextlib import ExitStack, closing, suppress
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit


from .temporal import source_date


MAX_BYTES = 2_000_000
TIMEOUT = 12
REDIRECTS = {301, 302, 303, 307, 308}
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
SKIP = set("script style template noscript nav footer aside form svg iframe head".split())
BLOCK = set("p div article section main li br hr h1 h2 h3 h4 tr td ul ol".split())


class SourceUnavailable(ValueError):
    """The URL was accepted but yielded no usable material."""


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.published_at = None
        self.parts = {key: [] for key in ("title", "article", "main", "body")}

    def add(self, value):
        tags = {tag for tag, hidden in self.stack}
        if any(hidden for tag, hidden in self.stack):
            return
        if "title" in tags:
            self.parts["title"].append(value)
        elif not SKIP.intersection(tags):
            self.parts["body"].append(value)
            for tag in tags.intersection(("main", "article")):
                self.parts[tag].append(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and self.published_at is None:
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or ""
            if key.casefold() in {"article:published_time", "datepublished", "dc.date.issued"}:
                self.published_at = source_date(attrs.get("content"))
        if tag == "time" and self.published_at is None and attrs.get("itemprop") == "datePublished":
            self.published_at = source_date(attrs.get("datetime"))
        if tag in BLOCK:
            self.add("\n")
        if tag not in VOID:
            if len(self.stack) >= 128:
                raise SourceUnavailable("Zbyt głęboka struktura strony.")
            self.stack.append((tag, "hidden" in attrs or attrs.get("aria-hidden") == "true"))

    def handle_endtag(self, tag):
        if tag in BLOCK:
            self.add("\n")
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    handle_data = add


def extract(body, mime, charset, fallback_title, *, with_dates=False):
    try:
        decoder = codecs.lookup(charset or "utf-8")
        content = body.decode(decoder.name, errors="replace")
    except (LookupError, UnicodeError) as exc:
        raise SourceUnavailable("Nieobsługiwane kodowanie strony.") from exc
    title = fallback_title
    published_at = None
    if mime != "text/plain":
        parser = ArticleParser()
        try:
            parser.feed(content)
            parser.close()
        except AssertionError as exc:
            raise SourceUnavailable("Nie udało się odczytać struktury HTML.") from exc
        published_at = parser.published_at
        title = " ".join("".join(parser.parts["title"]).split()) or title
        content = ""
        for tag in ("main", "article", "body"):
            content = "".join(parser.parts[tag]).strip()
            if content:
                break
    text = "\n".join(line for raw in content.splitlines() if (line := " ".join(raw.split())))
    if not text:
        raise SourceUnavailable("Strona nie zawiera dostępnego tekstu.")
    if with_dates:
        return title[:500], text, {"published_at": published_at}
    return title[:500], text


def _target(url, allowed_hosts):
    if len(url) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise ValueError("Niepoprawny adres strony.")
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None or parts.fragment):
        raise ValueError("Podaj pełny adres HTTP lub HTTPS bez danych logowania i fragmentu #.")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if "%" in host or (allowed_hosts is not None and host not in allowed_hosts):
        raise ValueError("Niedozwolona domena.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("Dozwolone są standardowe porty HTTP i HTTPS.")
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not records:
        raise SourceUnavailable("Brak adresu serwera.")
    for _, _, _, _, addr in records:
        ip = ipaddress.ip_address(addr[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError("Dozwolone są wyłącznie publiczne adresy serwerów.")
        if ip.version == 6 and (ip not in ipaddress.ip_network("2000::/3")
                                or ip.sixtofour or ip.teredo):
            raise ValueError("Niedozwolony zakres adresów IPv6.")
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
    authority = f"[{host}]" if ":" in host else host
    if port != (443 if parts.scheme == "https" else 80):
        authority += f":{port}"
    return urlunsplit((parts.scheme, authority, path, query, "")), host, port, records[0]


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise SourceUnavailable("Minął limit czasu pobierania strony.")
    return left


def _interrupt_read(sock):
    with suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def _request(url, host, port, record, deadline, *, robots=False, with_dates=False):
    parts = urlsplit(url)
    family, kind, proto, _, address = record
    # Connect only to the resolved, validated sockaddr. TLS still verifies the hostname.
    with closing(socket.socket(family, kind, proto)) as raw:
        raw.settimeout(_remaining(deadline))
        raw.connect(address)
        conn = http.client.HTTPConnection(host, port)
        with closing(conn), ExitStack() as cleanup:
            conn.sock = (ssl.create_default_context().wrap_socket(raw, server_hostname=host)
                         if parts.scheme == "https" else raw)
            conn.sock.settimeout(_remaining(deadline))
            # A peer can trickle header bytes faster than the socket inactivity timeout.
            timer = Timer(_remaining(deadline), _interrupt_read, (conn.sock,))
            timer.daemon = True
            timer.start()
            cleanup.callback(timer.cancel)
            path = parts.path + ("?" + parts.query if parts.query else "")
            conn.request("GET", path, headers={"User-Agent": "Prawdowid/1.0",
                                               "Accept": "text/html,text/plain",
                                               "Accept-Encoding": "identity"})
            with conn.getresponse() as response:
                if response.status in REDIRECTS:
                    location = response.getheader("Location")
                    if not location:
                        raise SourceUnavailable("Przekierowanie bez adresu docelowego.")
                    return location, None
                if robots and response.status in {404, 410}:
                    return None, ""
                if response.status != 200:
                    raise SourceUnavailable(f"Serwer źródła zwrócił HTTP {response.status}.")
                mime = response.headers.get_content_type()
                if mime not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise SourceUnavailable("Strona musi zawierać HTML lub zwykły tekst.")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise SourceUnavailable("Nieobsługiwana kompresja odpowiedzi.")
                body = bytearray()
                while len(body) <= MAX_BYTES:
                    _remaining(deadline)
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - len(body)))
                    if not chunk:
                        if robots:
                            return None, body.decode("utf-8-sig", errors="replace")
                        return None, extract(body, mime, response.headers.get_content_charset(), host, with_dates=with_dates)
                    body.extend(chunk)
                raise SourceUnavailable("Strona przekracza limit rozmiaru.")


def fetch(url, *, allowed_hosts=None, with_dates=False):
    """Fetch a readable public page, validating every hop before opening a socket."""
    deadline = time.monotonic() + TIMEOUT
    try:
        for _ in range(5):
            url, host, port, record = _target(url, allowed_hosts)
            if allowed_hosts is not None:
                robots_url = urljoin(url, "/robots.txt")
                redirect, rules = _request(robots_url, host, port, record, deadline, robots=True)
                if redirect:
                    raise ValueError("Nie udało się ustalić reguł robots.txt.")
                parser = RobotFileParser()
                parser.parse(rules.splitlines())
                if not parser.can_fetch("Prawdowid", url):
                    raise ValueError("Indeksowanie zabronione przez robots.txt.")
                delay = parser.crawl_delay("Prawdowid") or 0
                if delay >= _remaining(deadline):
                    raise ValueError("Opóźnienie robots.txt przekracza budżet pobrania.")
                time.sleep(delay)
            options = {"with_dates": True} if with_dates else {}
            location, result = _request(url, host, port, record, deadline, **options)
            if result is not None:
                return url, *result
            url = urljoin(url, location)
    except (OSError, http.client.HTTPException, UnicodeError) as exc:
        raise SourceUnavailable("Nie udało się połączyć ze stroną lub pobrać jej w wyznaczonym czasie.") from exc
    raise SourceUnavailable("Zbyt wiele przekierowań.")
