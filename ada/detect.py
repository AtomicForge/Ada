# SPDX-License-Identifier: GPL-3.0-or-later
"""Find Ollama and SearXNG on this computer ("Find automatically" button).

Only a fixed list of localhost addresses is tried, never a network scan.
Blocking: call from a background thread. A dead port fails at once; a silent
one costs the timeout.
"""
from ada.ollama import OllamaError, list_models
from ada.searxng import SearxError, search

OLLAMA_CANDIDATES = ("http://localhost:11434",)
SEARXNG_CANDIDATES = ("http://localhost:8080", "http://localhost:8888")
PROBE_TIMEOUT = 1.0  # Ollama answers at once
SEARX_TIMEOUT = 8.0  # SearXNG asks other search engines first


def find_ollama(candidates=OLLAMA_CANDIDATES, timeout=PROBE_TIMEOUT):
    """The first address that answers /api/tags like Ollama, or None."""
    for url in candidates:
        try:
            list_models(url, timeout=timeout)
        except OllamaError:
            continue
        return url
    return None


def find_searxng(candidates=SEARXNG_CANDIDATES, timeout=SEARX_TIMEOUT):
    """The first address that answers a JSON search like SearXNG, or None."""
    for url in candidates:
        try:
            search(url, "ada", timeout=timeout)
        except SearxError:
            continue
        return url
    return None
