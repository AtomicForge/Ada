# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP access to a local Ollama server: URL check, model list, streamed chat.

No GTK imports here, so this module can be tested without a display.
"""
import http.client
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 10.0
# The answer of a model list is tiny. Anything bigger is a broken or hostile server.
MAX_RESPONSE_BYTES = 1024 * 1024
# A streamed chat: how long we wait for the next piece. Ollama may need a while
# to load a model before the first word comes.
STREAM_IDLE_TIMEOUT = 120.0
MAX_LINE_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024
_ROLES = ("system", "user", "assistant")

# Networks where plain http is acceptable: loopback, private ranges, Tailscale.
_TRUSTED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",  # Tailscale (CGNAT range)
        "::1/128",
        "fc00::/7",  # IPv6 unique local addresses (Tailscale uses fd7a:...)
    )
)

_MSG_BAD_URL = "Ungültige Adresse. Beispiel: http://localhost:11434"
_MSG_TIMEOUT = "Zeitüberschreitung: Ollama antwortet nicht rechtzeitig."
_MSG_UNREACHABLE = "Ollama ist nicht erreichbar. Adresse, Port und Zertifikat prüfen."
_MSG_BROKEN = "Die Verbindung zu Ollama wurde unterbrochen."
_MSG_INVALID = "Ollama hat eine ungültige Antwort geschickt."
_MSG_CUT = "Die Antwort wurde vorzeitig beendet und ist unvollständig."
_MSG_BAD_REQUEST = "Ungültige Anfrage: Modell oder Nachrichten sind fehlerhaft."


class OllamaError(Exception):
    """Any problem talking to Ollama. The message is safe to show to the user."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """ADA talks only to the configured address, so redirects are refused."""

    def redirect_request(self, *args, **kwargs):
        return None


# Empty ProxyHandler: ignore http_proxy & co. from the environment, so traffic
# goes only where the user configured it.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def validate_url(url):
    """Check an Ollama address and return it normalized (no trailing slash).

    Raises OllamaError for anything that is not a plain http(s) address.
    """
    if not isinstance(url, str):
        raise OllamaError(_MSG_BAD_URL)
    url = url.strip()
    # Reject whitespace and control characters outright; urllib would
    # silently strip some of them.
    if not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise OllamaError(_MSG_BAD_URL)
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port  # raises ValueError for invalid ports
    except ValueError:
        raise OllamaError(_MSG_BAD_URL) from None
    host = parts.hostname
    if (
        parts.scheme not in ("http", "https")
        or not host
        or "@" in parts.netloc  # no user:password in the address
        or parts.query
        or parts.fragment
    ):
        raise OllamaError(_MSG_BAD_URL)
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return f"{parts.scheme}://{netloc}{parts.path.rstrip('/')}"


def needs_plaintext_warning(url):
    """True if the address is unencrypted http to somewhere outside trusted networks.

    Host names other than "localhost" are treated as untrusted: telling whether
    they point to a private network would need a DNS lookup, which ADA does not do.
    """
    parts = urllib.parse.urlsplit(validate_url(url))
    if parts.scheme != "http" or parts.hostname == "localhost":
        return False
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return True  # a host name
    return not any(address in net for net in _TRUSTED_NETWORKS)


def parse_models(raw):
    """Turn the bytes of a /api/tags answer into a sorted list of model names."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # includes bad UTF-8 and bad JSON
        raise OllamaError(_MSG_INVALID) from None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise OllamaError(_MSG_INVALID)
    names = set()
    for entry in models:
        name = entry.get("name") if isinstance(entry, dict) else None
        # Names are shown in the UI: keep only sane, printable ones.
        if isinstance(name, str) and 0 < len(name) <= 200 and name.isprintable():
            names.add(name)
    return sorted(names)


def _read_limited(response, deadline):
    """Read the body, but never more than MAX_RESPONSE_BYTES or past the deadline."""
    chunks = []
    size = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError
        chunk = response.read1(65536)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise OllamaError(_MSG_INVALID)
        chunks.append(chunk)


def list_models(base_url, timeout=DEFAULT_TIMEOUT):
    """Ask Ollama (GET /api/tags) for its installed models. Blocking: call it
    from a background thread, never from the UI thread."""
    url = validate_url(base_url) + "/api/tags"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    deadline = time.monotonic() + timeout  # total limit, not just per read
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = _read_limited(response, deadline)
    except urllib.error.HTTPError as exc:  # must come before URLError
        exc.close()
        raise OllamaError(f"Ollama meldet Fehler {exc.code}.") from exc
    except TimeoutError as exc:
        raise OllamaError(_MSG_TIMEOUT) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise OllamaError(_MSG_TIMEOUT) from exc
        raise OllamaError(_MSG_UNREACHABLE) from exc
    except (http.client.HTTPException, OSError) as exc:
        raise OllamaError(_MSG_BROKEN) from exc
    return parse_models(raw)


def _server_message(text):
    """Text from the server, made safe to show: printable only, shortened."""
    text = "".join(c for c in text if c.isprintable())[:200]
    return f"Ollama meldet: {text}" if text else _MSG_INVALID


def _error_from_body(raw, status):
    """Error text from the body of a non-200 answer ({"error": "..."} if any)."""
    try:
        text = json.loads(raw.decode("utf-8")).get("error")
    except (ValueError, AttributeError, RecursionError):
        text = None
    if isinstance(text, str) and text:
        return _server_message(text)
    return f"Ollama meldet Fehler {status}."


def _clean_chat_input(model, messages):
    """Check the request; keep only role and content of each message."""
    ok = isinstance(model, str) and 0 < len(model) <= 200 and model.isprintable()
    ok = ok and isinstance(messages, list) and len(messages) > 0
    ok = ok and all(
        isinstance(m, dict)
        and m.get("role") in _ROLES
        and isinstance(m.get("content"), str)
        for m in messages
    )
    if not ok:
        raise OllamaError(_MSG_BAD_REQUEST)
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def _parse_chat_line(line):
    """One line of the stream -> (text piece, done). Raises OllamaError if broken."""
    try:
        data = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError):  # includes bad UTF-8 and bad JSON
        raise OllamaError(_MSG_INVALID) from None
    if not isinstance(data, dict):
        raise OllamaError(_MSG_INVALID)
    if "error" in data:
        error = data["error"]
        raise OllamaError(_server_message(error if isinstance(error, str) else ""))
    message = data.get("message")
    text = message.get("content") if isinstance(message, dict) else None
    return (text if isinstance(text, str) else ""), data.get("done") is True


def _split_lines(response):
    """Yield the lines of the answer. Memory use is limited."""
    buffer = b""
    total = 0
    while True:
        chunk = response.read1(65536)
        if not chunk:
            yield buffer  # the last line may come without a newline
            return
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise OllamaError(_MSG_INVALID)
        *lines, buffer = (buffer + chunk).split(b"\n")
        if len(buffer) > MAX_LINE_BYTES:
            raise OllamaError(_MSG_INVALID)
        yield from lines


class ChatStream:
    """One streamed answer from POST /api/chat.

    Iterate over it to get the answer piece by piece, as plain text. Do this in
    a background thread, never in the UI thread. cancel() may be called from any
    thread (the stop button): the iteration then ends quietly, and the caller
    keeps the text received so far. If the stream fails, OllamaError is raised
    after the pieces that already arrived. One ChatStream, one answer.
    """

    def __init__(self, base_url, model, messages,
                 idle_timeout=STREAM_IDLE_TIMEOUT, connect_timeout=DEFAULT_TIMEOUT):
        self._parts = urllib.parse.urlsplit(validate_url(base_url))
        self._body = json.dumps(
            {"model": model, "messages": _clean_chat_input(model, messages),
             "stream": True}
        ).encode("utf-8")
        self._idle_timeout = idle_timeout
        self._connect_timeout = connect_timeout
        self._cancelled = threading.Event()
        self._conn = None

    def cancel(self):
        """Stop at once, even while waiting for the server. Safe to call twice."""
        self._cancelled.set()
        sock = getattr(self._conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # wakes up a blocked read
            except OSError:
                pass  # already closed

    def __iter__(self):
        if self._cancelled.is_set():
            return
        parts = self._parts
        # http.client follows no redirects and uses no proxy from the environment.
        if parts.scheme == "https":
            connection = http.client.HTTPSConnection  # checks the certificate
        else:
            connection = http.client.HTTPConnection
        conn = self._conn = connection(
            parts.hostname, parts.port, timeout=self._connect_timeout
        )
        started = False  # True once the server has answered
        try:
            conn.connect()
            if self._cancelled.is_set():  # cancel() came while connecting
                return
            conn.sock.settimeout(self._idle_timeout)
            conn.request(
                "POST", parts.path + "/api/chat", self._body,
                {"Content-Type": "application/json",
                 "Accept": "application/x-ndjson"},
            )
            response = conn.getresponse()
            started = True
            if response.status != 200:
                raise OllamaError(_error_from_body(response.read(4096), response.status))
            for line in _split_lines(response):
                if self._cancelled.is_set():
                    return
                if not line.strip():
                    continue
                text, done = _parse_chat_line(line)
                if text:
                    yield text
                if done:
                    return
            if not self._cancelled.is_set():  # closed without the final "done"
                raise OllamaError(_MSG_CUT)
        except OllamaError:
            raise
        except TimeoutError as exc:
            if not self._cancelled.is_set():
                raise OllamaError(_MSG_TIMEOUT) from exc
        except (http.client.HTTPException, OSError) as exc:
            if not self._cancelled.is_set():
                raise OllamaError(_MSG_BROKEN if started else _MSG_UNREACHABLE) from exc
        finally:
            conn.close()
