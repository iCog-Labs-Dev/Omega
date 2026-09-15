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

from conftest import PLUGIN_DIR

_CHANNEL = PLUGIN_DIR / "telegram_channel.py"

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


def _template_of(context: Path) -> str:
    """The proxy template as run-plugin left it in the build context."""
    return (context / "proxy" / "nginx.conf.template").read_text()


def test_the_bot_downloads_through_the_route_run_plugin_looks_for():
    """The bot's file base and run-plugin's check have to name one path.

    run-plugin derives the path it looks for from the plugin directory name,
    <plugin>-file; the bot builds its file base in Python. Nothing lines the two
    up at runtime, and a mismatch just 404s.
    """
    match = re.search(r'file=f"\{proxy\}/([\w-]+)/', _CHANNEL.read_text())
    assert match, "no TelegramAPIServer file base found in telegram_channel.py"
    assert match.group(1) == f"{PLUGIN_DIR.name}-file"


def test_the_plugin_ships_no_nginx_fragment():
    """The route is core's. A conf here would be spliced back in by an old
    run-plugin, and two identical locations in one server block is a config
    nginx refuses to start on."""
    assert not (PLUGIN_DIR / "proxy.conf").exists()


def test_a_core_that_routes_downloads_is_left_untouched(fake_core, prepare):
    """Preparing against a good core changes nothing about its proxy config."""
    core = fake_core(nginx_template=_TEMPLATE % _CORE_ROUTE)
    before = (core / "proxy" / "nginx.conf.template").read_text()

    said, context = prepare(core)

    template = _template_of(context)
    assert template == before
    assert template.count("location /telegram-file/ {") == 1
    assert "telegram-file" not in said


def test_a_core_without_the_route_is_reported_not_patched(fake_core, prepare):
    """No route is a core problem, so run-plugin names it and carries on.

    It must not fail the build — the bot still runs, text still works — and it
    must not reach into core's template to fix it, which is the workaround this
    replaced.
    """
    core = fake_core(nginx_template=_TEMPLATE % "")
    before = (core / "proxy" / "nginx.conf.template").read_text()

    said, context = prepare(core)

    template = _template_of(context)
    assert template == before
    assert "location /telegram-file/" not in template
    assert "no /telegram-file/ route in core's nginx template." in said
    assert "404" in said


def test_cores_route_survives_envsubst(fake_core, prepare):
    """The token lands and nginx's own $1 does not.

    Core renders the template with envsubst before starting nginx. It expands
    ${TG_BOT_TOKEN} and has to leave the capture group alone — substituting $1
    would send every download to /file/bot<token>/ with no path. The route is
    core's to define, but this is the shape the bot's downloads depend on.
    """
    _, context = prepare(fake_core(nginx_template=_TEMPLATE % _CORE_ROUTE))
    rendered = subprocess.run(
        ["envsubst", "${TG_BOT_TOKEN}"],
        input=_template_of(context), capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "TG_BOT_TOKEN": "12345:SECRET"},
    ).stdout
    assert "/file/bot12345:SECRET/$1 break;" in rendered
