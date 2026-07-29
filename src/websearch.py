#!/usr/bin/env python3
import json
import os
import urllib.parse
import urllib.request

try:
    from src.logger import get_logger
except ModuleNotFoundError:  # imported directly with src/ on the path
    from logger import get_logger

logger = get_logger(__name__)

MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_FIELD_CHARS = 300
MAX_OUTPUT_CHARS = 8000

_STRIP = str.maketrans("", "", '()"')

def _clean(value):
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    value = value.translate(_STRIP)
    value = " ".join(value.split())
    return value[:MAX_FIELD_CHARS]

def search(query, max_results=10):
    searxng_url = os.environ.get("SEARXNG_URL", "").strip()
    if not searxng_url:
        logger.warning("SEARXNG_URL is not set; websearch returning empty result")
        return ""
    try:
        params = urllib.parse.urlencode({"q": query, "format": "json"})
        url = urllib.parse.urljoin(searxng_url.rstrip("/") + "/", "search") + "?" + params
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))
        ret = "("
        for r in data.get("results", [])[:max_results]:
            if not isinstance(r, dict):
                continue
            title = _clean(r.get("title", ""))
            snippet = _clean(r.get("content", ""))
            entry = f"(TITLE: {title} SNIPPET: {snippet}) "
            if len(ret) + len(entry) + 1 > MAX_OUTPUT_CHARS:  # +1 for closing )
                break
            ret += entry
        ret += ")"
        return ret
    except Exception as e:
        logger.exception(f"Web search failed for query {query!r}: {e}")
        return ""
