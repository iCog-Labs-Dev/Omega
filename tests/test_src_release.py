import json
import urllib.error

import pytest

import config
import release

TAG = release.ReleaseTag("prod-2026-07-17")

PAYLOAD = {
    "tag_name": "prod-2026-07-17",
    "name": "Omega-prod-2026-07-17",
    "html_url": "https://github.com/iCog-Labs-Dev/Omega/releases/tag/prod-2026-07-17",
    "published_at": "2026-07-20T09:41:53Z",
    "body": "## [prod-2026-07-17]\n\n### Added\n- Telegram image support",
}


@pytest.fixture(autouse=True)
def clean_config():
    config._CONFIG = {}
    yield
    config._CONFIG = {}


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / release.MARKER_FILE
    monkeypatch.setattr(release, "marker_path", lambda: path)
    return path


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(release.channels, "commChannelSend", messages.append)
    return messages


@pytest.fixture
def released_build(monkeypatch):
    monkeypatch.setattr(release, "omega_version",
                        lambda: "Omega version=prod-2026-07-17")


def build(version):
    return release.ReleaseTag.of_build(f"Omega version={version}")


def test_tag_of_a_released_build():
    assert build("prod-2026-07-17") == TAG
    assert build("v0.1.19") == release.ReleaseTag("v0.1.19")
    assert build("v0.1.6b") == release.ReleaseTag("v0.1.6b")
    assert build(" prod-2026-07-17 \n") == TAG


def test_no_tag_when_the_build_is_not_a_release():
    assert build("prod-2026-07-17-45-g1234567") is None
    assert build("v0.1.19-4-g1234567") is None
    assert build("v0.1.19-dirty") is None
    assert build("8822974") is None
    assert build("") is None
    assert release.ReleaseTag.of_build("Omega unknown") is None


def test_the_words_before_the_version_do_not_matter():
    # helper.omega_version() puts the project name in front of the tag, and
    # images built before a rename carry the older one, so only the marker counts.
    assert release.ReleaseTag.of_build("Omega version=prod-2026-07-17") == TAG
    assert release.ReleaseTag.of_build("Anything at all version=prod-2026-07-17") == TAG


def test_a_named_release_overrides_the_tag_of_the_build(monkeypatch):
    monkeypatch.setenv("OMEGA_releaseTag", "prod-2026-07-17")
    monkeypatch.setattr(release, "omega_version",
                        lambda: "Omega version=prod-2026-07-17-45-g1234567")
    assert release.announcing_tag() == TAG


def test_summary_request_carries_the_notes_and_the_link():
    request = release.summary_request(TAG, PAYLOAD)
    assert release.SUMMARY_INSTRUCTIONS in request
    assert "VERSION: prod-2026-07-17" in request
    assert f"TITLE: {PAYLOAD['name']}" in request
    assert PAYLOAD["html_url"] in request
    assert request.endswith("- Telegram image support")


def test_nothing_to_summarize_without_notes():
    assert release.summary_request(TAG, None) == ""
    assert release.summary_request(TAG, {}) == ""
    assert release.summary_request(TAG, dict(PAYLOAD, body="   ")) == ""


def test_the_published_notes_fit_whole():
    # The one published release has a 6001-character body; it used to lose its
    # last character to the cap.
    body = "x" * 6001
    assert release.fit_notes(body) == body


def test_long_notes_are_trimmed_back_to_a_blank_line():
    paragraph = "x" * 100
    kept = release.fit_notes("\n\n".join([paragraph] * 400))
    assert len(kept) <= release.MAX_NOTES_CHARS
    assert kept.endswith(paragraph)
    assert "\n\n" in kept


def test_a_title_then_an_unbroken_bullet_run_keeps_the_bullets():
    body = "## [prod-2026-09-03]\n\n" + "\n".join(["- change " + "x" * 60] * 600)
    kept = release.fit_notes(body)
    assert len(kept) >= release.MAX_NOTES_CHARS * 4 // 5
    assert kept.count("- change ") > 200
    assert kept.endswith("x")


def test_notes_with_no_blank_line_fall_back_to_a_line_break():
    kept = release.fit_notes("\n".join(["y" * 100] * 400))
    assert len(kept) <= release.MAX_NOTES_CHARS
    assert kept.endswith("y")


def test_announcement_escapes_newlines_the_way_channels_expect():
    assert release.announcement("Omega just updated\n- one thing\r\n- another") == \
        "Omega just updated\\n- one thing\\n- another"


def test_a_heading_after_bullets_keeps_its_gap():
    # Telegram's converter drops the blank line that ends a list, so the line
    # before a heading carries a zero-width space to hold the gap open.
    assert release.announcement("- one\n- two\n\n**Fixes**\n\n- three") == \
        "- one\\n- two\\n\u200b\\n\\n**Fixes**\\n\\n- three"


def test_a_heading_after_a_paragraph_is_left_alone():
    assert release.announcement("An overview line.\n\n**Fixes**\n\n- one") == \
        "An overview line.\\n\\n**Fixes**\\n\\n- one"


def test_announcement_of_an_unusable_summary():
    assert release.announcement("") == ""
    assert release.announcement("   \n  ") == ""


def test_announce_sends_once_and_records_it(marker, sent, released_build, monkeypatch):
    monkeypatch.setattr(release, "fetch_release", lambda tag: PAYLOAD)
    monkeypatch.setattr(release, "_chat", lambda request: "Omega just updated\nsee the notes")

    assert release.announce() == "Omega just updated\\nsee the notes"
    assert sent == ["Omega just updated\\nsee the notes"]
    assert marker.read_text().strip() == "prod-2026-07-17"

    assert release.announce() == ""
    assert len(sent) == 1


def test_announce_stays_quiet_on_a_build_that_is_not_a_release(marker, sent, monkeypatch):
    monkeypatch.setattr(release, "omega_version",
                        lambda: "Omega version=prod-2026-07-17-45-g1234567")
    monkeypatch.setattr(release, "fetch_release",
                        lambda tag: pytest.fail("asked GitHub about a non-release"))
    monkeypatch.setattr(release, "_chat", lambda request: pytest.fail("asked the model"))

    assert release.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_announce_stays_quiet_when_github_has_no_release(marker, sent, released_build, monkeypatch):
    monkeypatch.setattr(release, "fetch_release", lambda tag: None)
    monkeypatch.setattr(release, "_chat", lambda request: pytest.fail("asked the model"))

    assert release.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_a_release_stays_unannounced_when_the_model_fails(marker, sent, released_build, monkeypatch):
    monkeypatch.setattr(release, "fetch_release", lambda tag: PAYLOAD)

    def refuse(request):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(release, "_chat", refuse)

    assert release.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_a_release_stays_unannounced_when_the_summary_is_empty(marker, sent, released_build, monkeypatch):
    monkeypatch.setattr(release, "fetch_release", lambda tag: PAYLOAD)
    monkeypatch.setattr(release, "_chat", lambda request: "   ")

    assert release.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_a_failed_fetch_leaves_nothing_to_announce(monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(release.urllib.request, "urlopen", refuse)
    assert release.fetch_release(TAG) is None


class _Answer:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_fetch_reads_the_tag_endpoint(monkeypatch):
    seen = {}

    def answer(request, timeout=None):
        seen["url"] = request.full_url
        return _Answer()

    monkeypatch.setattr(release.urllib.request, "urlopen", answer)
    monkeypatch.setattr(release.json, "load", lambda response: PAYLOAD)

    assert release.fetch_release(TAG) == PAYLOAD
    assert seen["url"] == ("https://api.github.com/repos/iCog-Labs-Dev/Omega"
                           "/releases/tags/prod-2026-07-17")


def test_fetch_follows_the_configured_repository(monkeypatch):
    seen = {}

    def answer(request, timeout=None):
        seen["url"] = request.full_url
        return _Answer()

    monkeypatch.setenv("OMEGA_releaseApiURL", "https://ghe.example.com/api/v3/")
    monkeypatch.setenv("OMEGA_releaseRepo", "/someone/elsewhere/")
    monkeypatch.setattr(release.urllib.request, "urlopen", answer)
    monkeypatch.setattr(release.json, "load", lambda response: PAYLOAD)

    release.fetch_release(release.ReleaseTag("v9.9.9"))
    assert seen["url"] == "https://ghe.example.com/api/v3/repos/someone/elsewhere/releases/tags/v9.9.9"
