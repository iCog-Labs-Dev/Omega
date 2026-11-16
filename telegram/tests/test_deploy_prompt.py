"""The deploy hands the bot the image's prompt and leaves Oma's folder as it was.

The bot runs on the old Oma agent's memory folder, which holds Oma's own
prompt.txt. Core reads memory/prompt_<provider>.txt first and prompt.txt after
it, so the deploy mounts the image's prompt over those names. QA found that an
earlier deploy mounted onto names the folder did not have: Docker made an empty
file for each, and an empty prompt_<provider>.txt would start a rolled-back
release with no prompt at all.

These run the deploy's own shell block from deploy.yml, with docker replaced by
a stand-in that reads a folder playing the image.
"""
import os
import shutil
import subprocess

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOY = os.path.join(os.path.dirname(os.path.dirname(_HERE)), ".github", "workflows", "deploy.yml")
_MEMORY = "/PeTTa/repos/Omega/memory/"

pytestmark = pytest.mark.skipif(
    not os.path.exists(_DEPLOY) or shutil.which("bash") is None,
    reason="needs the repository's deploy workflow and bash")

# `docker run --rm --entrypoint sh -e K=V ... IMAGE -c SCRIPT` runs SCRIPT with
# $MEMORY_DIR pointing at the folder that plays the image.
_FAKE_DOCKER = """
docker() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -e) export "$2"; shift 2 ;;
      -c) MEMORY_DIR="$FAKE_IMAGE" sh -c "$2"; return ;;
      *) shift ;;
    esac
  done
}
"""


def _prompt_block():
    """The lines of the deploy step that pick and mount the prompt."""
    with open(_DEPLOY) as handle:
        workflow = yaml.safe_load(handle)
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run") or ""
            if "memory_mounts" in script:
                lines = script.splitlines()
                start = next(i for i, line in enumerate(lines) if line.startswith("memory="))
                end = next(i for i, line in enumerate(lines)
                           if line.startswith("echo \"the image's prompt"))
                return "\n".join(lines[start:end + 1])
    raise AssertionError("no step in deploy.yml builds memory_mounts")


def _deploy(tmp_path, image, folder, provider="OpenRouter"):
    """Run the block; return (exit code, mounts as {target name: source})."""
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    for name, text in image.items():
        (image_dir / name).write_text(text)
    memory = tmp_path / "memory"
    memory.mkdir()
    for name, text in folder.items():
        (memory / name).write_text(text)
    env_dir = tmp_path / "env"
    env_dir.mkdir()

    block = _prompt_block().replace("memory=/Backup/docker/PeTTa", f"memory={memory}", 1)
    script = "set -e\n" + _FAKE_DOCKER + block + '\nprintf "%s\\n" ${memory_mounts}\n'
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "FAKE_IMAGE": str(image_dir),
             "env_dir": str(env_dir), "LLM_PROVIDER": provider, "IMAGE": "omega:test"})
    mounts = {}
    for word in result.stdout.split():
        if word.count(":") >= 1 and _MEMORY in word:
            source, target = word.split(":")[:2]
            mounts[target[len(_MEMORY):]] = source
    return result.returncode, mounts, memory


IMAGE = {"prompt.txt": "generic prompt",
         "prompt_OpenRouter.txt": "openrouter prompt",
         "prompt_ASICloud.txt": "asicloud prompt"}
OMA = {"prompt.txt": "self-chosen long-term goals. Do not idle."}


def _read(path):
    with open(path) as handle:
        return handle.read()


def test_the_bot_reads_the_image_prompt_for_its_provider(tmp_path):
    code, mounts, _ = _deploy(tmp_path, IMAGE, OMA)
    assert code == 0
    assert _read(mounts["prompt.txt"]) == "openrouter prompt"


def test_only_names_already_in_the_folder_are_mounted_over(tmp_path):
    code, mounts, memory = _deploy(tmp_path, IMAGE, OMA)
    assert code == 0
    # .env is mounted from /dev/null and exists in Oma's folder on the hosts.
    assert set(mounts) - {".env"} == {"prompt.txt"}
    assert all(name in os.listdir(memory) for name in set(mounts) - {".env"})


def test_oma_files_are_left_as_they_were(tmp_path):
    _, _, memory = _deploy(tmp_path, IMAGE, OMA)
    assert sorted(os.listdir(memory)) == ["prompt.txt"]
    assert _read(memory / "prompt.txt") == OMA["prompt.txt"]


def test_an_empty_file_left_by_an_earlier_deploy_is_covered(tmp_path):
    # Core would read it before prompt.txt, so the bot would get no prompt.
    folder = {**OMA, "prompt_OpenRouter.txt": ""}
    code, mounts, _ = _deploy(tmp_path, IMAGE, folder)
    assert code == 0
    assert _read(mounts["prompt_OpenRouter.txt"]) == "openrouter prompt"
    assert "prompt_ASICloud.txt" not in mounts


def test_a_provider_without_its_own_prompt_gets_the_general_one(tmp_path):
    code, mounts, _ = _deploy(tmp_path, IMAGE, OMA, provider="Anthropic")
    assert code == 0
    assert _read(mounts["prompt.txt"]) == "generic prompt"


def test_an_image_with_no_prompt_stops_the_deploy(tmp_path):
    code, _, _ = _deploy(tmp_path, {}, OMA)
    assert code != 0
