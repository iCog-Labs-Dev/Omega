"""Render Graphviz DOT source to a PNG without calling an image model."""
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 64_000
MAX_PNG_BYTES = 10_000_000
MAX_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MiB
MAX_CPU_SECONDS = 10
MAX_RETAINED_DIAGRAMS = 20
MAX_ARTIFACT_AGE_SECONDS = 24 * 60 * 60
RENDER_TIMEOUT_SECONDS = 15
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def prune_diagrams(root, keep_count):
    """Remove expired or excess diagram folders without following symlinks."""
    cutoff = time.time() - MAX_ARTIFACT_AGE_SECONDS
    folders = sorted(
        (
            folder
            for folder in root.glob("diagram-*")
            if not folder.is_symlink() and folder.is_dir()
        ),
        key=lambda folder: folder.stat().st_mtime,
        reverse=True,
    )
    for index, folder in enumerate(folders):
        if index >= keep_count or folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder)


def render_diagram(source, output_directory, retain_artifacts=False):
    """Return render status, retaining artifact paths only when enabled.

    output_directory is supplied by the host's memoryDirectory setting, not
    by the model. Graphviz inherits the host's filesystem security policy.
    """
    if not isinstance(source, str) or not source.strip():
        return "DIAGRAM_FAILED: provide nonempty Graphviz DOT source"

    executable = shutil.which("dot")
    if executable is None:
        return "DIAGRAM_FAILED: Graphviz is not installed; install dot and add it to PATH"

    limiter = shutil.which("prlimit")
    if limiter is None:
        return (
            "DIAGRAM_FAILED: safe rendering requires Linux prlimit "
            "(util-linux); use the Docker runtime"
        )

    artifact_dir = None
    keep_artifacts = False
    retain = (
        retain_artifacts is True
        or str(retain_artifacts).strip().lower() == "true"
    )
    try:
        encoded_source = source.encode("utf-8")
        if len(encoded_source) > MAX_SOURCE_BYTES:
            raise ValueError("DOT source exceeds 64000 UTF-8 bytes")
        root = Path(output_directory) / "diagrams"
        root.mkdir(parents=True, exist_ok=True)
        # Reserve one place for this render. Prune before creating its folder.
        prune_diagrams(root, MAX_RETAINED_DIAGRAMS - 1 if retain else 0)
        artifact_dir = Path(tempfile.mkdtemp(prefix="diagram-", dir=root))
        image_path = artifact_dir / "diagram.png"
        source_path = artifact_dir / "diagram.dot"
        source_path.write_text(source, encoding="utf-8")
        # Source goes through stdin, never through a shell or command arguments.
        result = subprocess.run(
            [
                limiter,
                f"--as={MAX_MEMORY_BYTES}",
                f"--cpu={MAX_CPU_SECONDS}",
                f"--fsize={MAX_PNG_BYTES}",
                "--core=0",
                "--",
                executable,
                "-Kdot",
                "-Tpng",
                "-o",
                str(image_path.resolve()),
            ],
            input=encoded_source,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=artifact_dir,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                "Graphviz could not render this DOT source "
                "or exceeded a resource limit"
            )
        size = image_path.stat().st_size
        with image_path.open("rb") as image:
            signature = image.read(len(PNG_SIGNATURE))
        if signature != PNG_SIGNATURE or size <= len(PNG_SIGNATURE):
            raise ValueError("Graphviz did not produce a PNG image")
        if size > MAX_PNG_BYTES:
            raise ValueError("rendered PNG exceeds 10 MB; simplify the diagram")
        if retain:
            keep_artifacts = True
            return (
                f"DIAGRAM_RENDERED: file={image_path.resolve()} "
                f"source={source_path.resolve()} bytes={size}; "
                "saved locally, not sent to the user"
            )
        return (
            "DIAGRAM_RENDERED: temporary artifacts removed; "
            "not retained or sent to the user"
        )
    except subprocess.TimeoutExpired:
        error = "Graphviz exceeded the 15-second rendering timeout"
    except (OSError, ValueError) as exc:
        error = str(exc)
    finally:
        if artifact_dir is not None and not keep_artifacts:
            try:
                shutil.rmtree(artifact_dir)
            except OSError:
                logger.exception("Failed to remove diagram staging directory")
                return (
                    "DIAGRAM_FAILED: staging cleanup failed; "
                    "check filesystem permissions"
                )
    logger.warning("Diagram rendering failed: %s", error)
    return f"DIAGRAM_FAILED: {error}"
