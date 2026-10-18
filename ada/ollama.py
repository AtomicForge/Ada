# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP access to a local Ollama server: URL check and model list.

No GTK imports here, so this module can be tested without a display.
"""
import http.client
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 10.0
# The answer of a model list is tiny. Anything bigger is a broken or hostile server.
MAX_RESPONSE_BYTES = 1024 * 1024

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
