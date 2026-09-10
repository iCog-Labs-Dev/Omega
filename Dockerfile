# Adds the Telegram plugin's Python dependencies to a core image.
#
# Core installs only its own requirements.txt, so aiogram, Pillow, pypdf and
# telegramify-markdown are missing and the plugin loader fails on import. The
# plugin itself is not copied in — the launcher mounts it at runtime, so it can
# be edited without a rebuild. Only the dependencies need to be baked in.
#
# The runtime stage of the core image has no pip, hence the apt-get.
ARG BASE_IMAGE=omega-core:7f0d33f
FROM ${BASE_IMAGE}

USER root
COPY telegram/requirements.txt /tmp/plugin-requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-pip \
 && python3 -m pip install --no-cache-dir --break-system-packages \
      -r /tmp/plugin-requirements.txt \
 && rm -rf /var/lib/apt/lists/* /tmp/plugin-requirements.txt
