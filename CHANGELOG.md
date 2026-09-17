# Changelog

Each release has a section headed by its tag. The deploy publishes that section
as the GitHub release, and the bot summarises it to users on its first start.
A `pre-prod-*` tag rehearses the `prod-*` section of the same date on staging.

## prod-2026-09-17

### Added

* Plugin-based Telegram integration under `plugins/telegram/`, replacing the previous Telegram-specific core implementation.
* Outbound Telegram voice replies, synthesised with edge-tts and sent as a native voice message, with the recording indicator refreshing while they are produced.
* Automatic language detection on the reply text, with a matching voice chosen for it. A mixed-language reply is spoken in its main language throughout, so foreign words carry that accent rather than switching voice mid-sentence.
* Sentence-aware chunking for long speech, falling back to word boundaries, so a long reply is spoken in order instead of being cut.
* Markdown, links (including bare domains), emoji and symbols are stripped before synthesis, so they are not read aloud.
* `EDGE_TTS_VOICE` to pick the speaking voice, defaulting to `en-US-AriaNeural`. An unknown or misspelled voice falls back to the default instead of failing.
* Release announcements on first startup after deployment of a newly published GitHub release.
* Configurable release repository and release tag for startup announcements.
* Workflow plugin support for loading skills from Markdown-based workflow definitions.
* Workflow loading and unloading.
* Research workflow support.
* Centralized configuration through `config/config.yaml`.
* Per-provider LLM model configuration.
* `OMEGA_` environment-variable overrides for configuration values.
* Plugin subscription to agent-loop events.
* Verified `write-file` and `append-file` operations that read written content back from disk.
* `write-file-b64`, for binary-safe and escaping-safe file writes.
* Mock and regression test suites for Telegram, Slack, WebSocket, workflows, memory, RAG, file I/O, skills, Git operations, OpenClaw delegation, and provider behavior.
* ARM64 and platform-specific CI test execution.
* Expanded documentation for channels, configuration, orchestration, memory, reasoning, plugins, and skills.
* Tutorials for memory, shell and file operations, custom skills, channel development, and reasoning workflows.

### Changed

* The `telegram` branch now sits on the current Omega core architecture.
* Telegram functionality moved from core-specific code into the standalone Telegram plugin.
* `channels/tg_channel.py` -> `plugins/telegram/telegram_channel.py`.
* `memory/telegram_profile.yaml` -> `plugins/telegram/telegram_profile.yaml`.
* `memory/tg_prompt.txt` -> `plugins/telegram/prompt.txt`.
* Telegram policy configuration moved into `plugins/telegram/policy.md`.
* LLM provider infrastructure moved from the root `lib_llm_ext.py` into `providers/` and provider-specific modules.
* OpenAI, OpenRouter, ASI:One, OpenAI-compatible, and mock providers split into separate modules.
* LLM calls unified around the current provider interfaces.
* Telegram media processing, transcription, image generation, vision fallback, and media context handling moved into the Telegram plugin.
* Plugin dependencies install from plugin-specific requirements.
* Runtime configuration prefers the central configuration API over direct environment variables for non-secret values.
* Web search results include source URLs with titles and snippets.
* Context-frame prompting separates frame-specific instructions from the base prompt.
* Multiline and multi-form command argument parsing improved.
* CI workflows support configurable registry and image names.
* Production and staging deployment workflows use authenticated GHCR pulls.
* Telegram deployment configuration uses plugin-oriented environment and runtime settings.
* Release, manual, cleanup, Renovate, Sonar, and automated-test workflows expanded.
* Docker builds cover the current plugin and provider architecture.
* Frame relation embeddings use the configured or default embedding model.

### Fixed

* An empty `speak` argument produced an empty voice note.
* Replies over Telegram's 4096-character limit, now split before they enter the delivery queue.
* Message splitting measured raw text length rather than rendered MarkdownV2 length.
* A long reply blocked every message queued behind it.
* Non-retryable delivery errors stayed in the outbox indefinitely.
* PNG, JPEG, and WebP files uploaded as documents skipped the image-processing pipeline.
* Image-document captions were lost during processing.
* Invalid raster files and SVG/SVGZ documents reached the image-processing pipeline.
* `/togglesearch` reported search as disabled without preventing web-search execution.
* `/pause` failed when configured chat IDs and incoming chat IDs used different data types.
* `/purge` ignored the configured Chroma database path.
* `/purge` left stale cached Chroma collection state after deletion.
* `/kill`, `/purge`, and `/togglesearch` bypassed the expected authorization handshake.
* `/about` responded outside configured chats.
* Paused chats received unsupported-media notices.
* Image captions replaced the image-context marker.
* Ethics-alert delivery failures were silently ignored.
* Missing admin recipients for ethics alerts were silently ignored.
* Gateway proxy handling for file-download routes.
* Frame completion re-executed repeatedly during nondeterministic evaluation.
* Frame completion hung during relation searches with no valid related frames.
* Parsing of multiple top-level MeTTa command forms returned by the LLM.
* Zero-argument command forms were corrupted when followed by additional forms.
* Parentheses inside quoted strings interfered with command-form parsing.
* False-positive syntax-error detection for valid command responses.
* Mocked LLM responses failed to match context-frame requests.
* OpenAI runtime embedding behavior, now with mandatory CI coverage.
* `append-file` concatenated new content onto files with no trailing newline.
* Append handling for empty files and files ending with multibyte UTF-8 characters.
* File-write operations returned success without verifying the resulting contents.
* Standalone channel authentication handling.
* Authentication string handling errors.
* Prompt-grounding tests were missing from mandatory CI execution.
* Runtime embedding tests were missing from mandatory CI execution.

### Security

* Authorization checks on destructive Telegram administrative commands.
* Startup warnings when Telegram admin IDs or allowed chat IDs remain unset.
* Validation and rejection of unsupported or malformed image documents.
* Image sanitization before images are passed to the agent (unchanged).
* Ethics checks for document and audio content (unchanged).
* Restricted media handling and controlled media-context injection (unchanged).
* Production access scoped through `admin_controls.admin_ids` and `telegram.allowed_chats`.

### Removed

* The legacy root `lib_llm_ext.py`, in favor of the provider-based architecture.
* `lib_omegaclaw.metta`, in favor of the current Omega runtime structure.
* The legacy Telegram prompt at `memory/tg_prompt.txt`.
* The legacy Telegram policy location at `memory/policy.md`.
* The legacy Telegram channel implementation at `channels/tg_channel.py`.
* The legacy context builder and the `useFrame` configuration path.
* Obsolete Agentverse bridge files no longer used by the current core.

### Configuration

* Telegram access is configured in `plugins/telegram/telegram_profile.yaml`.
* `admin_controls.admin_ids` must be set before production deployment.
* `telegram.allowed_chats` must be set before production deployment.
* Voice replies are opt-in through `telegram.reply_constraints.allow_voice_reply`.
* `EDGE_TTS_VOICE` overrides the voice where a fixed one is wanted instead of the detected language's.
* Provider credentials are required for transcription, vision, and image generation. Voice synthesis needs no key.
* Docker containers must be rebuilt and recreated to pick up the new plugin dependencies and architecture.
* GHCR credentials are required when pulling private deployment images.
* A GitHub Release must be published in addition to the release tag, or startup announcements stay inactive.

### Migration

* `channels/tg_channel.py` -> `plugins/telegram/telegram_channel.py`
* `memory/telegram_profile.yaml` -> `plugins/telegram/telegram_profile.yaml`
* `memory/tg_prompt.txt` -> `plugins/telegram/prompt.txt`
* Custom policy references -> `plugins/telegram/policy.md`
* Provider integrations referencing the root `lib_llm_ext.py`
* Deployment scripts, Docker volume mounts, and prompt overrides that depend on the previous Telegram file locations
