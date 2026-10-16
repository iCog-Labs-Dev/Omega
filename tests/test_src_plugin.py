import pytest

import plugin


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


BASE = """\
- name: irc
  loader: python
  location: "{REPO}/channels"
- name: telegram
  loader: python
  location: "{REPO}/channels"
- name: workflow
  loader: metta
  location: "{REPO}/plugins/workflow"
"""


def _names(triples):
    return [name for _, name, _ in triples]


def test_without_drop_ins_lists_plugins_yaml(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)

    triples = plugin._list_plugins(tmp_path)

    assert _names(triples) == ["irc", "telegram", "workflow"]
    assert triples[0] == ["python", "irc", str(plugin._REPO / "channels")]


def test_empty_drop_in_directory_changes_nothing(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    (tmp_path / "plugins.d").mkdir()
    _write(tmp_path / "plugins.d" / "empty.yaml", "")

    assert _names(plugin._list_plugins(tmp_path)) == ["irc", "telegram", "workflow"]


def test_drop_in_adds_a_plugin_at_the_end(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    _write(tmp_path / "plugins.d" / "extra.yaml",
           "- name: extra\n  loader: metta\n  location: /opt/extra\n")

    triples = plugin._list_plugins(tmp_path)

    assert _names(triples) == ["irc", "telegram", "workflow", "extra"]
    assert triples[-1] == ["metta", "extra", "/opt/extra"]


def test_drop_in_replaces_a_plugin_in_place(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    _write(tmp_path / "plugins.d" / "telegram.yaml",
           '- name: telegram\n  loader: metta\n  location: "{REPO}/plugins/telegram"\n')

    triples = plugin._list_plugins(tmp_path)

    assert _names(triples) == ["irc", "telegram", "workflow"]
    assert triples[1] == ["metta", "telegram", str(plugin._REPO / "plugins" / "telegram")]


def test_drop_in_turns_a_plugin_off(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    _write(tmp_path / "plugins.d" / "no-irc.yaml", "- name: irc\n  enabled: false\n")

    assert _names(plugin._list_plugins(tmp_path)) == ["telegram", "workflow"]


def test_later_drop_in_wins(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    _write(tmp_path / "plugins.d" / "10-off.yaml", "- name: workflow\n  enabled: false\n")
    _write(tmp_path / "plugins.d" / "20-on.yaml",
           "- name: workflow\n  loader: metta\n  location: /opt/workflow\n")

    triples = plugin._list_plugins(tmp_path)

    assert triples[-1] == ["metta", "workflow", "/opt/workflow"]


def test_non_yaml_files_are_ignored(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    _write(tmp_path / "plugins.d" / "README", "- name: irc\n  enabled: false\n")

    assert _names(plugin._list_plugins(tmp_path)) == ["irc", "telegram", "workflow"]


def test_duplicate_name_in_one_file_is_refused(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE + "- name: irc\n  loader: python\n")

    with pytest.raises(RuntimeError, match="not unique"):
        plugin._list_plugins(tmp_path)


def test_bad_loader_in_drop_in_is_refused(tmp_path):
    _write(tmp_path / "plugins.yaml", BASE)
    _write(tmp_path / "plugins.d" / "bad.yaml", "- name: bad\n  loader: lua\n")

    with pytest.raises(RuntimeError, match="incorrect loader"):
        plugin._list_plugins(tmp_path)


def test_shipped_manifest_still_loads():
    names = _names(plugin.listPlugins())

    assert "telegram" in names and "workflow" in names
