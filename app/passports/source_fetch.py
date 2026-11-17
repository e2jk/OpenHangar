"""Bounded public HTTP fetches with DNS-pinned sockets and verified TLS.

No proxy environment, cookies, credentials, linked-resource loading or remote JS.
Each redirect is validated independently; one absolute deadline covers DNS, connect,
TLS, headers and body. Resolver concurrency is bounded even if system DNS stalls.
"""

import contextlib
import fcntl
import http.client
import ipaddress
import queue
import socket
import ssl
import threading
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from flask import current_app, has_app_context
from flask_babel import gettext as _
from services.upload_layout import (  # pyright: ignore[reportMissingImports]
    PASSPORT_FETCH_LOCKS_DIRNAME,
)

from passports.management import BuilderError

MAX_WIRE_BYTES = 1_000_000
MAX_DECODED_BYTES = 2_000_000
TOTAL_SECONDS = 15.0
DNS_SECONDS = 3.0
MAX_REDIRECTS = 3
_DNS_SLOTS = threading.BoundedSemaphore(2)
_FETCH_SLOTS = threading.BoundedSemaphore(2)


class SourceError(BuilderError):
    """Safe message suitable for the import form."""


def _failure() -> SourceError:
    return SourceError(
        _("The source could not be fetched safely. Paste the airfield codes instead.")
    )


def _unsupported() -> SourceError:
    return SourceError(
        _(
            "The source response is too large or unsupported. Paste the airfield codes instead."
        )
    )


@contextlib.contextmanager
def fetch_slot() -> Iterator[None]:
    """Fail fast above two fetches, across workers sharing instance uploads.

    flock is tied to each open file description, so separate threads/processes
    contend correctly and a crashed worker releases its slots automatically.
    Retain the empty lock files: unlinking them could split the lock namespace.
    """

    def busy() -> SourceError:
        return SourceError(
            _("Other source imports are busy. Try again shortly or paste the codes."),
            409,
        )

    if not _FETCH_SLOTS.acquire(blocking=False):
        raise busy()
    try:
        if not has_app_context():
            yield
            return
        folder = (
            Path(current_app.config["UPLOAD_FOLDER"]) / PASSPORT_FETCH_LOCKS_DIRNAME
        )
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for slot in range(2):
            with (folder / f"slot-{slot}.lock").open("a+b") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                yield
                return
        raise busy()
    except OSError as exc:
        raise _failure() from exc
    finally:
        _FETCH_SLOTS.release()


@dataclass(frozen=True)
class Target:
    url: str
    host: str
    port: int
    secure: bool
    path: str


def validate_url(url: str) -> Target:
    try:
        if (
            len(url) > 512
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
            or "\\" in url
        ):
            raise ValueError
        parsed = urlsplit(url)
        secure = parsed.scheme == "https"
        host = (
            (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        )
        port = parsed.port if parsed.port is not None else (443 if secure else 80)
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or "@" in parsed.netloc
            or "%" in host
            or port != (443 if secure else 80)
        ):
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        canonical = urlunsplit((parsed.scheme, authority, path, query, ""))
        if len(canonical) > 512:
            raise ValueError
        return Target(
            canonical, host, port, secure, path + ("?" + query if query else "")
        )
    except (UnicodeError, ValueError) as exc:
        raise SourceError(
            _("Only public HTTP(S) source URLs on standard ports are supported.")
        ) from exc


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _failure()
    return remaining


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_reserved or ip.is_multicast:
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            return (
                ip.ipv4_mapped is None
                and ip.sixtofour is None
                and ip.teredo is None
                and ip in ipaddress.ip_network("2000::/3")
            )
        return True
    except ValueError:
        return False


def _lookup(host: str, port: int, result: queue.Queue[Any]) -> None:
    try:
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)
    finally:
        _DNS_SLOTS.release()


def resolve(target: Target, deadline: float) -> tuple[int, str]:
    _remaining(deadline)
    if not _DNS_SLOTS.acquire(blocking=False):
        raise _failure()
    result: queue.Queue[Any] = queue.Queue(maxsize=1)
    threading.Thread(
        target=_lookup, args=(target.host, target.port, result), daemon=True
    ).start()
    try:
        answers = result.get(timeout=min(DNS_SECONDS, _remaining(deadline)))
    except queue.Empty as exc:
        raise _failure() from exc
    if isinstance(answers, OSError) or not answers:
        raise _failure()
    addresses = []
    for family, _type, _proto, _name, sockaddr in answers:
        address = sockaddr[0]
        if family not in {socket.AF_INET, socket.AF_INET6} or not _public(address):
            raise _failure()
        addresses.append((family, address))
    return addresses[0]


class PinnedConnection(http.client.HTTPConnection):
    def __init__(
        self, target: Target, address: tuple[int, str], deadline: float
    ) -> None:
        super().__init__(target.host, target.port, timeout=min(5, _remaining(deadline)))
        self.target = target
        self.address = address
        self.deadline = deadline
        self.abort_socket: socket.socket | None = None

    def connect(self) -> None:
        family, address = self.address
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.settimeout(min(5, _remaining(self.deadline)))
        self.sock.connect((address, self.port))
        # Keep an independent shutdown handle: HTTPResponse may own the body
        # socket after HTTPConnection closes its reference, including during TLS.
        self.abort_socket = self.sock.dup()
        if self.target.secure:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.sock = context.wrap_socket(self.sock, server_hostname=self.host)

    def abort(self) -> None:
        transport = self.abort_socket or self.sock
        if transport is not None:
            with contextlib.suppress(OSError):
                transport.shutdown(socket.SHUT_RDWR)

    def finish(self) -> None:
        self.close()
        if self.abort_socket is not None:
            self.abort_socket.close()
            self.abort_socket = None


def _read(response: http.client.HTTPResponse, deadline: float) -> bytes:
    length = response.getheader("Content-Length")
    if length is not None and (
        not length.isascii()
        or not length.isdigit()
        or len(length) > 10
        or int(length) > MAX_WIRE_BYTES
    ):
        raise _unsupported()
    encoding = (response.getheader("Content-Encoding") or "identity").lower().strip()
    if encoding not in {"identity", "gzip", "deflate"}:
        raise _unsupported()
    decoder = (
        None
        if encoding == "identity"
        else zlib.decompressobj(zlib.MAX_WBITS | (16 if encoding == "gzip" else 0))
    )
    wire = 0
    output = bytearray()
    while True:
        _remaining(deadline)
        chunk = response.read1(65536)
        if not chunk:
            break
        wire += len(chunk)
        if wire > MAX_WIRE_BYTES:
            raise _unsupported()
        decoded = (
            decoder.decompress(chunk, MAX_DECODED_BYTES + 1 - len(output))
            if decoder
            else chunk
        )
        output.extend(decoded)
        if len(output) > MAX_DECODED_BYTES or (decoder and decoder.unused_data):
            raise _unsupported()
    if decoder and not decoder.eof:
        raise _unsupported()
    return bytes(output)


@dataclass(frozen=True)
class Fetched:
    url: str
    content_type: str
    body: bytes


def fetch(url: str, *, deadline: float | None = None) -> Fetched:
    with fetch_slot():
        return _fetch(url, deadline=deadline)


def _fetch(url: str, *, deadline: float | None = None) -> Fetched:
    deadline = deadline if deadline is not None else time.monotonic() + TOTAL_SECONDS
    seen = set()
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            target = validate_url(url)
            if target.url in seen:
                raise _failure()
            seen.add(target.url)
            connection = PinnedConnection(target, resolve(target, deadline), deadline)
            timer = threading.Timer(_remaining(deadline), connection.abort)
            timer.daemon = True
            timer.start()
            response = None
            try:
                connection.request(
                    "GET",
                    target.path,
                    headers={
                        "User-Agent": "OpenHangar passport importer",
                        "Accept": "text/html, application/json, text/plain",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise _failure()
                    next_target = validate_url(urljoin(target.url, location))
                    if target.secure and not next_target.secure:
                        raise _failure()
                    url = next_target.url
                    continue
                if response.status != 200:
                    raise _failure()
                content_type = (
                    (response.getheader("Content-Type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type not in {
                    "text/html",
                    "application/json",
                    "text/plain",
                    "application/xhtml+xml",
                }:
                    raise _unsupported()
                body = _read(response, deadline)
                _remaining(deadline)
                return Fetched(target.url, content_type, body)
            finally:
                timer.cancel()
                if response is not None:
                    response.close()
                connection.finish()
    except (OSError, http.client.HTTPException, zlib.error, UnicodeError) as exc:
        raise _failure() from exc
    raise _failure()
