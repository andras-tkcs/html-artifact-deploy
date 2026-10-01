"""html_artifact_deploy.fetch_client: which addresses the server downloads from. The network is always faked."""

from __future__ import annotations

import io
import logging
import socket
import urllib.request
import urllib.response
from email.message import Message
from typing import Any

import pytest

from html_artifact_deploy.config import FetchConfig
from html_artifact_deploy.fetch_client import (
    MAX_REDIRECTS,
    USER_AGENT,
    FetchClient,
    FetchClientError,
    _AllowlistRedirectHandler,
)

pytestmark = pytest.mark.unit

HOSTS = ("raw.example.com", "files.example.org")
URL = "https://raw.example.com/org/repo/page.html"
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
BAD_URL = (
    "url must be an https:// address with no user name, password or port, "
    "for example https://raw.githubusercontent.com/org/repo/main/page.html."
)


def resolver(*addresses: str) -> Any:
    def resolve(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
        out: list[tuple[Any, ...]] = []
        for address in addresses:
            if ":" in address:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)))
        return out

    return resolve


def public(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
    return PUBLIC


class Body(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_called = False

    def read(self, size: int | None = -1) -> bytes:
        self.read_called = True
        return super().read(size)


class FakeHTTPS(urllib.request.HTTPSHandler):
    """Answers from url -> (code, headers, body); records every request it sees."""

    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes] | Exception]) -> None:
        super().__init__()
        self.routes = routes
        self.requests: list[urllib.request.Request] = []
        self.bodies: list[Body] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        self.requests.append(req)
        route = self.routes[req.full_url]
        if isinstance(route, Exception):
            raise route
        code, raw_headers, body = route
        headers = Message()
        for name, value in raw_headers.items():
            headers[name] = value
        stream = Body(body)
        self.bodies.append(stream)
        resp = urllib.response.addinfourl(stream, headers, req.full_url, code)
        resp.msg = "OK"
        return resp


def client(
    fake: FakeHTTPS, *, max_bytes: int = 1000, resolve: Any = public, hosts: tuple[str, ...] = HOSTS
) -> FetchClient:
    return FetchClient(FetchConfig(allowed_hosts=hosts, timeout_seconds=5), max_bytes, resolve=resolve, handlers=[fake])


def ok(body: bytes = b"<p>hi</p>", **headers: str) -> tuple[int, dict[str, str], bytes]:
    return (200, headers, body)


class TestFetch:
    async def test_returns_the_bytes_with_only_the_user_agent(self) -> None:
        fake = FakeHTTPS({URL: ok(b"<p>hi</p>")})
        assert await client(fake).fetch(URL) == b"<p>hi</p>"
        (request,) = fake.requests
        headers = {name.lower() for name, _ in request.header_items()}
        assert request.get_header("User-agent") == USER_AGENT
        assert "authorization" not in headers
        assert "cookie" not in headers
        assert request.get_method() == "GET"

    def test_proxy_environment_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
        fake = FakeHTTPS({URL: ok()})
        assert client(fake)._fetch_blocking(URL) == b"<p>hi</p>"
        assert len(fake.requests) == 1

    def test_log_names_the_host_and_not_the_path_or_query(self, caplog: pytest.LogCaptureFixture) -> None:
        url = f"{URL}?X-Amz-Signature=secret"
        fake = FakeHTTPS({url: ok(b"abc")})
        with caplog.at_level(logging.INFO, logger="html_artifact_deploy.fetch_client"):
            client(fake)._fetch_blocking(url)
        assert "fetched 3 bytes from raw.example.com" in caplog.text
        assert "secret" not in caplog.text
        assert "/org/repo" not in caplog.text


class TestCheckUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://raw.example.com/x",
            "https://user:pass@raw.example.com/",
            "https://@raw.example.com/",
            "https://raw.example.com/a\tb",
            "https://raw.example.com/a\nb",
            "https://raw.example.com/a b",
            "https://raw.example.com/\x7f",
            "https://[::1/",
            "https://raw.example.com:abc/",
            "https://raw.example.com:8443/",
            "https:///x",
            "",
        ],
    )
    def test_refused_address(self, url: str) -> None:
        with pytest.raises(FetchClientError) as excinfo:
            client(FakeHTTPS({})).check_url(url)
        assert str(excinfo.value) == BAD_URL

    def test_port_443_is_accepted_and_host_is_lowercased(self) -> None:
        assert client(FakeHTTPS({})).check_url("https://RAW.example.com:443/x") == "raw.example.com"

    def test_host_not_listed(self) -> None:
        with pytest.raises(FetchClientError) as excinfo:
            client(FakeHTTPS({})).check_url("https://evil.example.net/x")
        assert str(excinfo.value) == (
            "This server only fetches pages from raw.example.com, files.example.org. "
            "Ask whoever runs it to add evil.example.net to fetch.allowed_hosts, or pass the page as html."
        )

    def test_lookup_failure(self) -> None:
        def resolve(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
            raise socket.gaierror("nope")

        with pytest.raises(FetchClientError) as excinfo:
            client(FakeHTTPS({}), resolve=resolve).check_url(URL)
        assert str(excinfo.value) == "Could not look up raw.example.com."

    @pytest.mark.parametrize(
        "address", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "fe80::1%eth0", "64:ff9b::a00:1", "64:ff9b:1::1"]
    )
    def test_non_public_address_is_refused(self, address: str) -> None:
        with pytest.raises(FetchClientError) as excinfo:
            client(FakeHTTPS({}), resolve=resolver(address)).check_url(URL)
        assert str(excinfo.value) == (
            "raw.example.com resolves to a private or local address, so this server will not fetch from it."
        )

    def test_one_private_address_among_public_ones_is_refused(self) -> None:
        with pytest.raises(FetchClientError):
            client(FakeHTTPS({}), resolve=resolver("93.184.216.34", "10.0.0.5")).check_url(URL)

    def test_public_addresses_are_accepted(self) -> None:
        resolve = resolver("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")
        assert client(FakeHTTPS({}), resolve=resolve).check_url(URL) == "raw.example.com"


class TestRedirects:
    def test_redirect_to_another_allowed_host_is_followed(self) -> None:
        target = "https://files.example.org/p.html"
        fake = FakeHTTPS({URL: (302, {"Location": target}, b""), target: ok(b"moved")})
        assert client(fake)._fetch_blocking(URL) == b"moved"
        assert [r.full_url for r in fake.requests] == [URL, target]

    @pytest.mark.parametrize(
        ("location", "named"),
        [("https://evil.example.net/x", "evil.example.net"), ("http://raw.example.com/x", "raw.example.com")],
    )
    def test_redirect_to_a_refused_address(self, location: str, named: str) -> None:
        fake = FakeHTTPS({URL: (302, {"Location": location}, b"")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == f"The address redirected to {named}, which this server does not fetch from."
        assert len(fake.requests) == 1

    def test_redirect_without_a_host(self) -> None:
        fake = FakeHTTPS({URL: (302, {"Location": "https://@/x"}, b"")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert (
            str(excinfo.value) == "The address redirected to an invalid address, which this server does not fetch from."
        )

    def test_malformed_location(self) -> None:
        fake = FakeHTTPS({URL: (302, {"Location": "https://[::1/"}, b"")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "Could not fetch the page from raw.example.com."

    def test_file_location_is_an_http_error(self) -> None:
        fake = FakeHTTPS({URL: (302, {"Location": "file:///etc/passwd"}, b"")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "raw.example.com answered HTTP 302."

    def test_loop_is_an_http_error(self) -> None:
        other = "https://files.example.org/p.html"
        fake = FakeHTTPS({URL: (302, {"Location": other}, b""), other: (302, {"Location": URL}, b"")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "raw.example.com answered HTTP 302."

    def test_hop_counts_and_the_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        counts: list[int] = []
        original = _AllowlistRedirectHandler.redirect_request

        def spy(self: _AllowlistRedirectHandler, req: urllib.request.Request, *args: Any) -> Any:
            counts.append(len(getattr(req, "redirect_dict", {})))
            return original(self, req, *args)

        monkeypatch.setattr(_AllowlistRedirectHandler, "redirect_request", spy)
        urls = [f"https://raw.example.com/{n}" for n in range(MAX_REDIRECTS + 2)]
        routes: dict[str, Any] = {u: (302, {"Location": urls[i + 1]}, b"") for i, u in enumerate(urls[:-1])}
        routes[urls[-1]] = ok(b"end")
        fake = FakeHTTPS(routes)
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(urls[0])
        assert str(excinfo.value) == "The address redirected too many times."
        assert counts == [0, 1, 2, 3]
        assert len(fake.requests) == MAX_REDIRECTS + 1

    def test_three_redirects_succeed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = [f"https://raw.example.com/{n}" for n in range(MAX_REDIRECTS + 1)]
        routes: dict[str, Any] = {u: (302, {"Location": urls[i + 1]}, b"") for i, u in enumerate(urls[:-1])}
        routes[urls[-1]] = ok(b"end")
        assert client(FakeHTTPS(routes))._fetch_blocking(urls[0]) == b"end"


class TestFailures:
    def test_http_error_status_closes_the_body(self) -> None:
        fake = FakeHTTPS({URL: (404, {}, b"not here")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "raw.example.com answered HTTP 404."
        assert fake.bodies[0].closed

    def test_os_error(self) -> None:
        fake = FakeHTTPS({URL: OSError("boom")})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "Could not fetch the page from raw.example.com."

    def test_content_length_over_the_limit_is_refused_before_reading(self) -> None:
        fake = FakeHTTPS({URL: ok(b"x" * 10, **{"Content-Length": "1001"})})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "The page at that address is over the 1,000-byte limit."
        assert not fake.bodies[0].read_called
        assert fake.bodies[0].closed

    def test_non_numeric_content_length_is_ignored(self) -> None:
        fake = FakeHTTPS({URL: ok(b"abc", **{"Content-Length": "lots"})})
        assert client(fake)._fetch_blocking(URL) == b"abc"

    def test_one_byte_over_the_limit_without_content_length(self) -> None:
        fake = FakeHTTPS({URL: ok(b"x" * 1001)})
        with pytest.raises(FetchClientError) as excinfo:
            client(fake)._fetch_blocking(URL)
        assert str(excinfo.value) == "The page at that address is over the 1,000-byte limit."
        assert fake.bodies[0].closed

    def test_exactly_the_limit_is_accepted(self) -> None:
        fake = FakeHTTPS({URL: ok(b"x" * 1000)})
        assert client(fake)._fetch_blocking(URL) == b"x" * 1000

    def test_body_larger_than_one_chunk(self) -> None:
        fake = FakeHTTPS({URL: ok(b"y" * 150_000)})
        assert client(fake, max_bytes=200_000)._fetch_blocking(URL) == b"y" * 150_000
