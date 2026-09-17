# Changelog

Each release has a section headed by its tag. The deploy publishes that section
as the GitHub release, and the bot summarises it to users on its first start.
A `pre-prod-*` tag rehearses the `prod-*` section of the same date on staging,
and a trailing `.2`, `.3` and so on takes a fresh tag against the same notes.

These notes cover what people in the chat, and people running the bot, will
notice. Core changes that ship in the same image are recorded separately.

## prod-2026-09-17

### Added

* Voice replies. Ask the bot to say something out loud and it answers with a Telegram voice message, showing the recording indicator while it works.
* The language of a reply is detected and a matching voice chosen. A mixed-language reply is spoken in its main language throughout, so foreign words carry that accent instead of the voice switching mid-sentence.
* Long spoken replies are split on sentence boundaries, falling back to word boundaries, so they are spoken in order rather than cut off.
* Markdown, links including bare domains, emoji and symbols are stripped before speech, so they are not read aloud.
* `EDGE_TTS_VOICE` fixes the voice instead of using the detected one, defaulting to `en-US-AriaNeural`. An unknown or misspelled voice falls back to the default rather than failing.
* Images sent as uncompressed files. A PNG, JPEG or WebP sent as a document is now read as an image; before, only PDFs were accepted. SVG is refused by name, saying which formats do work.
* A summary of what changed, sent as the bot's first message after it is updated.
* A start-up warning when the configuration leaves the bot open. `restrict_to_config_chat` with an empty `allowed_chats` restricts nothing, and an empty `admin_ids` means nobody can run the admin commands and ethics alerts have nowhere to go.
* `TG_PROFILE_PATH` and `TG_POLICY_PATH`, so a deployment can ship its own channel profile and policy text without editing the bot's files.

### Changed

* The `/about` and `/privacy` text has been rewritten. It used to deny capabilities the bot has and describe one-minute context windows that were never implemented, and said nothing about where messages go. It now names the moderation, vision, transcription, image and speech services that see your text and attachments, and states that PDF text is extracted on the machine running the bot and then reaches the language model like any other text.
* Web search results carry the source URL alongside the title and snippet.
* Vision, image generation, transcription and moderation calls go through the gateway proxy when `GATEWAY_URL` is set, so those API keys belong to the proxy rather than the agent's environment.

### Fixed

* Replies over Telegram's 4096-character limit stalled the outbox. The message never sent, every answer queued behind it waited, and the agent went idle believing it had replied. One long answer was enough to silence the bot until it was restarted. Long replies are now split before they are queued, on the largest boundary that fits, measured on the rendered formatting rather than the raw text.
* Delivery errors that no retry could fix stayed in the outbox for ever, blocking everything behind them.
* A photo sent with a caption lost its image marker, so the bot replied that no image had come through while the image sat waiting. Captioning a photo with your question works now.
* `/pause` reported success and did nothing. Chat IDs arrive as numbers from Telegram and as text from the command and the config file, and the two never matched, so pausing was impossible wherever `allowed_chats` was set.
* `/purge` cleared a database at a fixed relative path instead of the one in use, and left the agent querying a deleted collection until restart.
* `/togglesearch` reported search as off while the agent kept searching.
* `/about` answered in any chat the bot was invited to, ignoring `restrict_to_config_chat`.
* A paused chat still received unsupported-media notices.
* An empty `speak` argument produced an empty voice note.
* Ethics alerts to admins failed silently. A bounced admin DM, or the shipped empty `admin_ids`, produced no alert and no error, which read as though no ethics blocks had happened.
* Invalid raster files and SVG documents reached the image-processing pipeline.

### Security

* `/kill`, `/purge` and `/togglesearch` bypassed the authorization handshake. With core's auth enabled they were the only paths reaching the agent without clearing it, and they are the three most destructive.
* Unsupported or malformed image documents are validated and refused before they are processed.
* Images are still stripped of metadata before they are passed to the agent, and ethics checks still run over document and audio content.

### Configuration

* Set `admin_controls.admin_ids` in `plugins/telegram/telegram_profile.yaml` before deploying to production. It ships empty.
* Set `telegram.allowed_chats` in the same file before deploying to production. It also ships empty, and an empty list restricts nothing.
* Voice replies are opt-in through `telegram.reply_constraints.allow_voice_reply`.
* Transcription, vision and image generation need provider credentials. Speech synthesis needs none.
* Rebuild and recreate the container when upgrading, to pick up the new plugin dependencies.
