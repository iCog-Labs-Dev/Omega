"""
Smoke (OMEGA-140): the web search skill actually returns results.

Runs `websearch.search` inside the container against live SearXNG,
so it catches failures where search silently returns an empty result.
"""
from helpers import dexec


def test_websearch_returns_results():
    # Wait for SearXNG independently of Compose's depends_on condition.
    healthz_code = """
import time
import urllib.request

deadline = time.time() + 30
while True:
    try:
        response = urllib.request.urlopen("http://searxng:8080/healthz", timeout=5)
        assert response.status == 200
        break
    except Exception:
        if time.time() >= deadline:
            raise
        time.sleep(2)
"""
    res = dexec("python3", "-c", healthz_code)
    assert res.returncode == 0, f"SearXNG healthz wait failed: {res.stderr!r}"

    search_code = (
        "import os, sys;"
        "sys.path.insert(0, os.path.join(os.environ['OMEGACLAW_DIR'], 'src'));"
        "import websearch;"
        "result = websearch.search('test');"
        "print('RESULT', result)"
    )
    res = dexec("python3", "-c", search_code)
    assert res.returncode == 0, f"search failed: {res.stderr!r}"
    result = next((line[len("RESULT "): ] for line in res.stdout.splitlines()
                   if line.startswith("RESULT ")), "")
    assert result.startswith("("), f"result does not start with '(': {result!r}"
    assert "TITLE:" in result, f"no TITLE: in result: {result!r}"
