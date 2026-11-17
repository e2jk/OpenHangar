"""SSRF, DNS pinning, TLS, deadlines and response/decompression bounds."""

import gzip
import io
import socket
import threading
import time
import zlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from passports import source_fetch as f


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://host/file",
        "http://user:pass@example.com/",
        "https://example.com:80/",
        "https://example.com:0/",
        "https://example.com:8443/",
        "http://example.com/\r\nX:evil",
        "https://exa\\mple.com",
        "http://[::1",
        "http://host%00/",
        "https://",
        "https://" + "x" * 510,
        "https://☃.example/" + "é" * 300,
    ],
)
def test_rejected_urls(url: str) -> None:
    with pytest.raises(f.SourceError):
        f.validate_url(url)


def test_url_canonicalization_and_ipv6() -> None:
    target = f.validate_url("https://EXAMPLE.com.:443/é?q=café#fragment")
    assert target.url == "https://example.com/%C3%A9?q=caf%C3%A9"
    assert target.path == "/%C3%A9?q=caf%C3%A9"
    assert (
        f.validate_url("http://[2606:4700:4700::1111]/").host == "2606:4700:4700::1111"
    )
    assert f.validate_url("http://example.com").port == 80


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:8.8.8.8",
        "2002:0808:0808::1",
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
        "64:ff9b::7f00:1",
        "invalid",
    ],
)
def test_private_reserved_and_transition_addresses(address: str) -> None:
    assert not f._public(address)


def answers(*addresses: str) -> list[tuple[Any, ...]]:
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443),
        )
        for address in addresses
    ]


def test_dns_mixed_answers_failure_and_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = f.validate_url("https://source.example/")
    with (
        patch.object(
            f.socket, "getaddrinfo", return_value=answers("8.8.8.8", "10.0.0.1")
        ),
        pytest.raises(f.SourceError),
    ):
        f.resolve(target, time.monotonic() + 1)
    with patch.object(
        f.socket, "getaddrinfo", return_value=answers("2606:4700:4700::1111")
    ):
        assert f.resolve(target, time.monotonic() + 1) == (
            socket.AF_INET6,
            "2606:4700:4700::1111",
        )
    for value in ([], OSError("DNS failed")):
        with (
            patch.object(
                f.socket,
                "getaddrinfo",
                side_effect=value if isinstance(value, OSError) else None,
                return_value=value,
            ),
            pytest.raises(f.SourceError),
        ):
            f.resolve(target, time.monotonic() + 1)
    with pytest.raises(f.SourceError):
        f.resolve(target, time.monotonic() - 1)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(f, "_DNS_SLOTS", slots)
    slots.acquire()
    with pytest.raises(f.SourceError):
        f.resolve(target, time.monotonic() + 1)
    slots.release()
    # A stalled resolver returns on budget, without waiting for thread shutdown.
    event = threading.Event()
    monkeypatch.setattr(f, "DNS_SECONDS", 0.01)
    with patch.object(
        f.socket,
        "getaddrinfo",
        side_effect=lambda *a, **kw: (event.wait(0.5), answers("8.8.8.8"))[1],
    ):
        try:
            with pytest.raises(f.SourceError):
                f.resolve(target, time.monotonic() + 1)
        finally:
            event.set()
            assert slots.acquire(timeout=1)
            slots.release()


def test_pinned_socket_and_verified_tls() -> None:
    target = f.validate_url("https://source.example/path")
    sock = MagicMock()
    context = MagicMock()
    context.wrap_socket.return_value = sock
    with (
        patch.object(f.socket, "socket", return_value=sock),
        patch.object(f.ssl, "create_default_context", return_value=context),
    ):
        connection = f.PinnedConnection(
            target, (socket.AF_INET, "8.8.8.8"), time.monotonic() + 10
        )
        connection.request("GET", target.path)
        sock.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(
            sock, server_hostname="source.example"
        )
        assert context.minimum_version == f.ssl.TLSVersion.TLSv1_2
        request = sock.sendall.call_args.args[0]
        assert b"Host: source.example" in request and b"8.8.8.8" not in request
        connection.abort()
        sock.dup.return_value.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        connection.finish()
    connection = f.PinnedConnection(
        f.validate_url("http://source.example/"),
        (socket.AF_INET, "8.8.8.8"),
        time.monotonic() + 10,
    )
    connection.abort()  # deadline may fire before connecting
    with patch.object(f.socket, "socket", return_value=sock):
        connection.connect()
        connection.close()
        sock.dup.return_value.shutdown.side_effect = OSError("closed")
        connection.abort()
        connection.finish()


class Response:
    def __init__(
        self, body: bytes = b"EBNM", *, status: int = 200, **headers: str
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": "text/html", **headers}
        self.stream = io.BytesIO(body)

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)

    def close(self) -> None:
        self.stream.close()

    def read1(self, size: int) -> bytes:
        return self.stream.read(size)


def test_decoding_and_size_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    deadline = time.monotonic() + 10
    for encoding, body in [
        ("gzip", gzip.compress(b"EBNM")),
        ("deflate", zlib.compress(b"EBNM")),
        ("identity", b"EBNM"),
    ]:
        assert (
            f._read(Response(body, **{"Content-Encoding": encoding}), deadline)
            == b"EBNM"
        )
    for response in [
        Response(**{"Content-Length": "x"}),
        Response(**{"Content-Length": "9" * 12}),
        Response(**{"Content-Length": str(f.MAX_WIRE_BYTES + 1)}),
        Response(**{"Content-Encoding": "br"}),
        Response(gzip.compress(b"EBNM")[:-2], **{"Content-Encoding": "gzip"}),
        Response(
            gzip.compress(b"EBNM") + gzip.compress(b"HEL"),
            **{"Content-Encoding": "gzip"},
        ),
    ]:
        with pytest.raises(f.SourceError):
            f._read(response, deadline)
    monkeypatch.setattr(f, "MAX_WIRE_BYTES", 10)
    with pytest.raises(f.SourceError):
        f._read(Response(b"x" * 11), deadline)
    monkeypatch.setattr(f, "MAX_WIRE_BYTES", 10000)
    monkeypatch.setattr(f, "MAX_DECODED_BYTES", 100)
    with pytest.raises(f.SourceError):
        f._read(
            Response(gzip.compress(b"x" * 10000), **{"Content-Encoding": "gzip"}),
            deadline,
        )


def test_fetch_redirect_validation_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    with (
        patch.object(f, "resolve", return_value=(socket.AF_INET, "8.8.8.8")) as resolve,
        patch.object(f, "PinnedConnection") as connection,
    ):
        connection.return_value.getresponse.side_effect = [
            Response(status=302, Location="/list"),
            Response(b"EBNM"),
        ]
        result = f._fetch("https://source.example/")
        assert result.url == "https://source.example/list" and result.body == b"EBNM"
        assert (
            resolve.call_count == 2 and connection.return_value.finish.call_count == 2
        )
        # Every redirect is resolved again; private or mixed targets never connect.
        # Stay on https so the hop reaches the address check, not the downgrade one.
        resolve.side_effect = [(socket.AF_INET, "8.8.8.8"), f.SourceError("blocked")]
        connection.return_value.getresponse.side_effect = [
            Response(status=302, Location="https://127.1/")
        ]
        before, resolved = connection.call_count, resolve.call_count
        with pytest.raises(f.SourceError, match="blocked"):
            f._fetch("https://source.example/")
        assert connection.call_count == before + 1
        assert resolve.call_count == resolved + 2
        # An https source never follows a redirect down to plain http, even to a
        # public host: the downgraded hop is refused before it is resolved.
        resolve.side_effect = None
        connection.return_value.getresponse.side_effect = [
            Response(status=302, Location="http://source.example/list")
        ]
        before, resolved = connection.call_count, resolve.call_count
        with pytest.raises(f.SourceError):
            f._fetch("https://source.example/")
        assert connection.call_count == before + 1
        assert resolve.call_count == resolved + 1
    with (
        patch.object(f, "resolve", return_value=(socket.AF_INET, "8.8.8.8")),
        patch.object(f, "PinnedConnection") as connection,
    ):
        for response in [
            Response(status=302),
            Response(status=404),
            Response(**{"Content-Type": "image/png"}),
            Response(b"bad gzip", **{"Content-Encoding": "gzip"}),
        ]:
            connection.return_value.getresponse.side_effect = None
            connection.return_value.getresponse.return_value = response
            with pytest.raises(f.SourceError):
                f._fetch("https://source.example/")
        connection.return_value.getresponse.side_effect = [
            Response(status=302, Location="/")
        ]
        with pytest.raises(f.SourceError):
            f._fetch("https://source.example/")
        monkeypatch.setattr(f, "MAX_REDIRECTS", 0)
        connection.return_value.getresponse.side_effect = None
        connection.return_value.getresponse.return_value = Response(
            status=302, Location="/next"
        )
        with pytest.raises(f.SourceError):
            f._fetch("https://source.example/")
        connection.return_value.request.side_effect = OSError("timeout")
        with pytest.raises(f.SourceError):
            f._fetch("https://source.example/")


def test_absolute_deadline_interrupts_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    event = threading.Event()
    monkeypatch.setattr(f, "TOTAL_SECONDS", 0.02)
    with (
        patch.object(f, "resolve", return_value=(socket.AF_INET, "8.8.8.8")),
        patch.object(f, "PinnedConnection") as connection,
    ):
        connection.return_value.abort.side_effect = event.set
        connection.return_value.getresponse.side_effect = lambda: (
            event.wait(1),
            Response(),
        )[1]
        with pytest.raises(f.SourceError):
            f._fetch("https://source.example/")
        assert event.is_set()
        connection.return_value.finish.assert_called_once()


@pytest.mark.parametrize(
    "host", ["127.1", "2130706433", "0x7f000001", "0177.0.0.1", "localhost"]
)
def test_alternate_host_spellings_follow_resolved_address(host: str) -> None:
    with (
        patch.object(f.socket, "getaddrinfo", return_value=answers("127.0.0.1")),
        pytest.raises(f.SourceError),
    ):
        f.resolve(f.validate_url("http://" + host + "/"), time.monotonic() + 1)


def test_fetch_admission_limits_and_releases(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(f, "_FETCH_SLOTS", slots)
    slots.acquire()
    slots.acquire()
    with (
        pytest.raises(f.SourceError, match="busy"),
        patch.object(f, "resolve") as resolver,
    ):
        f.fetch("https://source.example/")
    resolver.assert_not_called()
    slots.release()
    slots.release()
    # Make the local semaphore permissive to exercise the worker-shared file cap.
    monkeypatch.setattr(f, "_FETCH_SLOTS", threading.BoundedSemaphore(10))
    with app.app_context():
        with (
            f.fetch_slot(),
            f.fetch_slot(),
            pytest.raises(f.SourceError, match="busy"),
            f.fetch_slot(),
        ):
            pytest.fail("A third worker slot must not be admitted")
        with f.fetch_slot():
            pass
        with (
            patch.object(f.Path, "mkdir", side_effect=OSError("read only")),
            pytest.raises(f.SourceError),
            f.fetch_slot(),
        ):
            pytest.fail("No fetch may run without its admission lock")
        with f.fetch_slot():
            pass


def test_fetch_cap_is_shared_between_processes(app: Any) -> None:
    import os
    import subprocess
    import sys

    script = """from flask import Flask
from flask_babel import Babel
from passports.source_fetch import fetch_slot, SourceError
import sys
app = Flask('fetch-slot-probe')
app.config['UPLOAD_FOLDER'] = sys.argv[1]
Babel(app)
with app.app_context():
    try:
        with fetch_slot():
            sys.exit(2)
    except SourceError:
        sys.exit(0)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(f.Path(f.__file__).resolve().parents[1])
    with app.app_context(), f.fetch_slot(), f.fetch_slot():
        result = subprocess.run(
            [sys.executable, "-c", script, app.config["UPLOAD_FOLDER"]],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_https_downgrade_is_rejected_before_second_request() -> None:
    with (
        patch.object(
            f, "resolve", return_value=(socket.AF_INET, "8.8.8.8")
        ) as resolver,
        patch.object(f, "PinnedConnection") as connection,
    ):
        connection.return_value.getresponse.return_value = Response(
            status=302, Location="http://source.example/plain"
        )
        with pytest.raises(f.SourceError):
            f._fetch("https://source.example/")
        assert resolver.call_count == connection.call_count == 1


def test_fetch_admission_wraps_the_transport(app: Any) -> None:
    result = f.Fetched("https://source.example/", "text/plain", b"EBNM")
    with patch.object(f, "_fetch", return_value=result) as transport:
        with app.app_context():
            assert f.fetch("https://source.example/") == result
        with patch.object(f, "has_app_context", return_value=False):
            assert f.fetch("https://source.example/") == result
        assert transport.call_count == 2
