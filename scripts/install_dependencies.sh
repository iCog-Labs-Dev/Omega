#!/bin/sh
# Install core's Python dependencies, and those of every plugin that declares any.
#
# A plugin is a directory under plugins/. One that ships a requirements.txt gets
# it installed without core naming the plugin; one that ships none costs nothing.
#
# Everything reaches a single pip invocation. Separate invocations resolve
# separately, so a plugin pinning a version core also pins would replace core's
# copy in silence — core is not a pip distribution, so nothing records what it
# needed and no warning fires. Resolved together, a real conflict fails here
# rather than at import time, and pip check catches what resolution let through.
#
# Arguments are passed through to pip, so an image build can add its own:
#
#   ./scripts/install_dependencies.sh
#   ./scripts/install_dependencies.sh --no-cache-dir --break-system-packages
set -eu

repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

set -- "$@" -r "$repo/requirements.txt"
for plugin_requirements in "$repo"/plugins/*/requirements.txt; do
    if [ -f "$plugin_requirements" ]; then
        set -- "$@" -r "$plugin_requirements"
    fi
done

python3 -m pip install "$@"
python3 -m pip check
