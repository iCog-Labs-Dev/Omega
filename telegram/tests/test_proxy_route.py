"""The /telegram-file/ upstream route, and which side of the split owns it.

Telegram serves API methods from /bot<token>/ but files from /file/bot<token>/,
so downloads need a route of their own. Core carries that route in its nginx
template now; for a core that does not, run-plugin splices in the proxy.conf
shipped here. Both paths still exist, which is what these tests guard.

Getting it wrong fails two different ways, and neither is easy to read from the
symptom. With no route at all, downloads 404 while text keeps working, so it
looks like broken media handling rather than missing routing. Spliced into a
core that already has the route, nginx sees two `location /telegram-file/`
blocks in one server and refuses to start, taking the whole container with it.
"""
import re
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLUGIN_DIR = _HERE.parent
_REPO_ROOT = _PLUGIN_DIR.parent
_RUN_PLUGIN = _REPO_ROOT / "run-plugin"
_PROXY_CONF = _PLUGIN_DIR / "proxy.conf"
_CHANNEL = _PLUGIN_DIR / "telegram_channel.py"

_TEMPLATE = """\
http {
    server {
        listen 8080;

        location /telegram/ {
            rewrite ^/telegram/(.*)$ /bot${TG_BOT_TOKEN}/$1 break;
            proxy_pass https://api.telegram.org;
        }
%s    }
}
"""

# Core's own block, kept in step with proxy/nginx.conf.template. If core renames
# its location, this stops matching run-plugin's guard and the duplicate test
# fails — which is the warning we want, not a false alarm.
_CORE_ROUTE = """
        location /telegram-file/ {
            rewrite ^/telegram-file/(.*)$ /file/bot${TG_BOT_TOKEN}/$1 break;
            proxy_pass https://api.telegram.org;
            proxy_set_header Host api.telegram.org;
            proxy_http_version 1.1;
        }
"""


def _fake_core(root, routes=""):
    """The smallest tree run-plugin --prepare-only accepts as a core."""
    (root / "config").mkdir(parents=True)
    (root / "config" / "plugins.yaml").write_text(
        '- name: telegram\n  loader: python\n  location: "{REPO}/channels"\n'
    )
    (root / "Dockerfile").write_text("FROM scratch AS builder\nRUN :\n")
    (root / "proxy").mkdir(parents=True)
    (root / "proxy" / "nginx.conf.template").write_text(_TEMPLATE % routes)
    return root


def _prepare(tmp_path, core):
    """Run the build-context assembly and hand back the template it produced."""
    env_file = tmp_path / "env"
    env_file.write_text("TG_BOT_TOKEN=placeholder\n")
    cache = tmp_path / "cache"

    result = subprocess.run(
        [str(_RUN_PLUGIN), "--prepare-only",
         "--core-dir", str(core),
         "--plugin-dir", str(_PLUGIN_DIR),
         "--env-file", str(env_file)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
             "XDG_CACHE_HOME": str(cache)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (cache / "omega-plugin-run" / "context"
            / "proxy" / "nginx.conf.template").read_text()


def test_proxy_conf_routes_the_path_the_bot_downloads_from():
    """The route shipped here matches the file base the bot is built with.

    These are two files that have to agree on one string and nothing checks
    them against each other at runtime — a mismatch just 404s.
    """
    match = re.search(r'file=f"\{proxy\}/([\w-]+)/', _CHANNEL.read_text())
    assert match, "no TelegramAPIServer file base found in telegram_channel.py"

    routes = re.findall(r"location /([\w-]+)/ \{", _PROXY_CONF.read_text())
    assert match.group(1) in routes, (match.group(1), routes)


def test_proxy_conf_rewrites_to_telegrams_file_path():
    """/file/bot<token>/ is the prefix that makes this different from /telegram/."""
    assert re.search(
        r"rewrite \^/[\w-]+/\(\.\*\)\$ /file/bot\$\{TG_BOT_TOKEN\}/\$1",
        _PROXY_CONF.read_text(),
    )


def test_route_is_spliced_into_a_core_without_it(tmp_path):
    template = _prepare(tmp_path, _fake_core(tmp_path / "core"))
    assert template.count("location /telegram-file/ {") == 1


def test_route_is_not_duplicated_into_a_core_that_has_it(tmp_path):
    """Core carries the route itself now, so the splice has to stand down.

    Two identical locations in one server block is a config nginx rejects, so
    splicing regardless would trade a broken media path for a dead container.
    """
    core = _fake_core(tmp_path / "core", routes=_CORE_ROUTE)
    template = _prepare(tmp_path, core)
    assert template.count("location /telegram-file/ {") == 1


def test_spliced_route_survives_envsubst(tmp_path):
    """The token lands and nginx's own $1 does not.

    Core renders the template with envsubst before starting nginx. It expands
    ${TG_BOT_TOKEN} and has to leave the capture group alone — substituting $1
    would send every download to /file/bot<token>/ with no path.
    """
    template = _prepare(tmp_path, _fake_core(tmp_path / "core"))
    rendered = subprocess.run(
        ["envsubst", "${TG_BOT_TOKEN}"],
        input=template, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "TG_BOT_TOKEN": "12345:SECRET"},
    ).stdout
    assert "/file/bot12345:SECRET/$1 break;" in rendered
