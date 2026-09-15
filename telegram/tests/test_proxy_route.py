"""The /telegram-file/ upstream route, now that core is the only side that owns it.

Telegram serves API methods from /bot<token>/ but files from /file/bot<token>/,
so downloads need a route of their own. Core's nginx template carries it. The
plugin used to ship the block and have run-plugin splice it in; that is gone,
and these tests hold the line on what replaced it — the plugin asks for the
route, core defines it, and run-plugin only says so when a core does not.

The failure is worth stating because it does not look like what it is. Without
the route, downloads 404 while text messages keep working, so it reads as broken
media handling rather than missing routing. That is the whole reason run-plugin
still checks and says which it is.
"""
import re
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLUGIN_DIR = _HERE.parent
_REPO_ROOT = _PLUGIN_DIR.parent
_RUN_PLUGIN = _REPO_ROOT / "run-plugin"
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
# its location, run-plugin's check stops matching and the "left alone" test fails
# — which is the warning we want, not a false alarm.
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
    """Assemble the build context; hand back what run-plugin said and wrote."""
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
    template = (cache / "omega-plugin-run" / "context"
                / "proxy" / "nginx.conf.template").read_text()
    return result.stdout + result.stderr, template


def test_the_bot_downloads_through_the_route_run_plugin_looks_for():
    """The bot's file base and run-plugin's check have to name one path.

    run-plugin derives the path it looks for from the plugin directory name,
    <plugin>-file; the bot builds its file base in Python. Nothing lines the two
    up at runtime, and a mismatch just 404s.
    """
    match = re.search(r'file=f"\{proxy\}/([\w-]+)/', _CHANNEL.read_text())
    assert match, "no TelegramAPIServer file base found in telegram_channel.py"
    assert match.group(1) == f"{_PLUGIN_DIR.name}-file"


def test_the_plugin_ships_no_nginx_fragment():
    """The route is core's. A conf here would be spliced back in by an old
    run-plugin, and two identical locations in one server block is a config
    nginx refuses to start on."""
    assert not (_PLUGIN_DIR / "proxy.conf").exists()


def test_a_core_that_routes_downloads_is_left_untouched(tmp_path):
    """Preparing against a good core changes nothing about its proxy config."""
    core = _fake_core(tmp_path / "core", routes=_CORE_ROUTE)
    before = (core / "proxy" / "nginx.conf.template").read_text()

    said, template = _prepare(tmp_path, core)

    assert template == before
    assert template.count("location /telegram-file/ {") == 1
    assert "telegram-file" not in said


def test_a_core_without_the_route_is_reported_not_patched(tmp_path):
    """No route is a core problem, so run-plugin names it and carries on.

    It must not fail the build — the bot still runs, text still works — and it
    must not reach into core's template to fix it, which is the workaround this
    replaced.
    """
    core = _fake_core(tmp_path / "core")
    before = (core / "proxy" / "nginx.conf.template").read_text()

    said, template = _prepare(tmp_path, core)

    assert template == before
    assert "location /telegram-file/" not in template
    assert "no /telegram-file/ route in core's nginx template." in said
    assert "404" in said


def test_cores_route_survives_envsubst(tmp_path):
    """The token lands and nginx's own $1 does not.

    Core renders the template with envsubst before starting nginx. It expands
    ${TG_BOT_TOKEN} and has to leave the capture group alone — substituting $1
    would send every download to /file/bot<token>/ with no path. The route is
    core's to define, but this is the shape the bot's downloads depend on.
    """
    _, template = _prepare(tmp_path, _fake_core(tmp_path / "core", routes=_CORE_ROUTE))
    rendered = subprocess.run(
        ["envsubst", "${TG_BOT_TOKEN}"],
        input=template, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "TG_BOT_TOKEN": "12345:SECRET"},
    ).stdout
    assert "/file/bot12345:SECRET/$1 break;" in rendered
