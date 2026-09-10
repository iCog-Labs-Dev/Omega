# SNET TELEGRAM BOT

A Telegram bot with media support. It reads images, PDFs and voice notes that
users attach, and can generate images and send them back.

Everything ships as an Omega plugin: a `CommChannel` for the Telegram
channel plus two skills for the media capabilities. No core internals are
patched — `plugins.yaml` and `skills.metta` are mounted over their core copies
so the plugin and its skills are registered.

## Capabilities

| Capability | How the agent sees it |
| --- | --- |
| Inbound photo | Message shows `[image]`; the agent calls `describe-image` on demand |
| Inbound PDF | Extracted text is inlined into the message |
| Inbound voice / audio | Whisper transcript is inlined into the message |
| Outbound image | The agent calls `generate-image`, which generates and sends the photo |
| Admin commands | `/kill`, `/pause [chat_id]`, `/togglesearch`, `/purge` (admin IDs only) |
| Safety | Ethics classification on inbound and outbound text, per-user spam throttling |

## Installation

1. `git clone git@github.com:singnet/Omega_CustomBots.git`
2. `cd Omega_CustomBots`
3. `git checkout SNET_Telegram`
4. Open `Omega_Telegram_SNET` in an editor and replace every `'...'`:
   - `TG_BOT_TOKEN` — from @BotFather
   - `TG_CHAT_ID` — the chat the bot operates in
   - `ANTHROPIC_API_KEY` — reading images
   - `OPENROUTER_API_KEY` — chat, image generation, voice transcription
   - `OPENAI_API_KEY` — the Moderation API used by the safety checks
5. Edit `telegram/telegram_profile.yaml` and fill in **both** of these — they
   ship empty and the bot is not safe to run in a real chat until they are set:
   - `admin_controls.admin_ids` — Telegram user IDs allowed to run admin commands
   - `telegram.allowed_chats` — chat IDs the bot may operate in
6. In BotFather, turn **Group Privacy off** for the bot, otherwise it cannot see
   group messages that are not direct replies to it.
7. `./Omega_Telegram_SNET`

The first run builds core from source and takes a while. Later runs reuse the
image.

## Stop and restart

```sh
docker stop omega && docker start omega
```

Run only one instance per bot token. A second one makes Telegram reject both
with `TelegramConflictError`.

## Build notes

**Core is built from source, not pulled from a published tag.** This bot needs
core's plugin system (`config/plugins.yaml`, `src/channels.py`), which is not in
a release yet — `v0.1.17` has neither. `Omega_Telegram_SNET` clones core at
the pinned commit and builds it, then layers the plugin's Python dependencies
on top via the `Dockerfile`, from `telegram/requirements.txt`. Core installs
only its own `requirements.txt`, so without that layer the plugin loader fails
on import.

Omega core version: pinned commit `7f0d33f` of
`singnet/Omega`. Change `CORE_COMMIT` in
`Omega_Telegram_SNET` to move it.

**The launcher bypasses core's `entrypoint.sh`** with `--entrypoint sh`. That
entrypoint scrubs the environment down to an allowlist which excludes
`TG_BOT_TOKEN` and every API key, because core expects channels to read their
secrets from the nginx proxy it starts. This channel reads the environment
instead, so the entrypoint is skipped.

Two things follow, and one of them is worth understanding before deploying:

- **The keys stay in the agent process's environment.** A stock run keeps them
  in the proxy, where the agent cannot read them. Here anything that can read
  the process environment — including the agent's own `shell` skill — can read
  `TG_BOT_TOKEN` and all three API keys. Routing the channel through the proxy,
  the way core's `channels/telegram.py` does, would remove the need for the
  bypass entirely.
- `GATEWAY_URL` is unset, so providers call their APIs directly rather than
  through the proxy.

Skipping the entrypoint also skips the `su nobody` it ends with, which would
leave the agent running as **root**. The launcher runs the same `su nobody`
itself, so the process ends up as uid 65534 exactly as it would on a stock run;
`memoryDirectory` and the writable paths in `profile/policy.yaml` are all owned
by that user. Secrets handling is therefore the only way this run differs from a
stock one — not privileges.

## Files

| Path | Mounted over | Role |
| --- | --- | --- |
| `telegram/` | `plugins/telegram` | The plugin: channel, media handling, vision, config |
| `plugins.yaml` | `config/plugins.yaml` | Core's plugin list plus this plugin's entry |
| `skills.metta` | `src/skills.metta` | Core's skills plus `describe-image` and `generate-image` |
| `knowledge-priors/` | `knowledge-priors` | Shared knowledge base, inherited from `main` |
| `Dockerfile` | — | Adds the plugin's Python dependencies to the core image |
| `Omega_Telegram_SNET` | — | Builds and runs the bot |

`skills.metta` carries the two media skills because a MeTTa plugin's
`loadOmegaPlugin` cannot currently register skills that reach `getSkills`.
Once that is fixed upstream they can move into the plugin and this file no
longer needs mounting.

`knowledge-priors/` comes from the `main` branch — merge `main` into this branch
to pick up updates. Core discovers it automatically: `src/rag.py` reads
`<repo root>/knowledge-priors/*.md`, and skips the step if the folder is absent.

## Configuration

Behaviour lives in `telegram/telegram_profile.yaml`: reply gating, admin IDs,
spam thresholds, ethics categories, and `reply_constraints` (which includes
`allow_image_generation`). `telegram/policy.md` holds the `/start`, `/about` and
`/privacy` text.

Optional environment variables:

| Variable | Purpose |
| --- | --- |
| `VISION_PROVIDER` | `Anthropic` (default) or `OpenRouter` |
| `VISION_MODEL` | Overrides the vision provider's default model |
| `IMAGE_PROVIDER` | `OpenRouter` (default, FLUX) or `OpenAI` |
| `IMAGE_MODEL` | Overrides the image provider's default model |

Vision defaults to Anthropic: an OpenRouter account whose data policy excludes
vision providers gets a 404 on every vision model, while text chat and image
generation keep working on the same key.

## Tests

```sh
cd telegram && for f in tests/test_*.py; do python3 "$f"; done
```

Plain asserts, no framework. Network and Telegram calls are stubbed, so no
credentials are needed.

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) file for details.
