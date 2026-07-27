# omegaclaw-telegram

A Telegram communication channel for OmegaClaw with media support: the agent can
read images, PDFs and voice notes that users attach, and can generate images and
send them back.

## Capabilities

| Capability | How the agent sees it |
| --- | --- |
| Inbound photo | Message shows `[image]`; the agent calls `describe-image` on demand |
| Inbound PDF | Extracted text is inlined into the message |
| Inbound voice / audio | Whisper transcript is inlined into the message |
| Outbound image | The agent calls `generate-image`, which generates and sends the photo |
| Admin commands | `/kill`, `/pause [chat_id]`, `/togglesearch`, `/purge` (admin IDs only) |
| Safety | Ethics classification on inbound and outbound text, per-user spam throttling |

## Install

**1. Register the plugin** in core's `config/plugins.yaml`:

```yaml
- name: telegram_media
  loader: python
  location: "{REPO}/plugins/media"
```

**2. Register the two skills** in core's `src/skills.metta`:

```metta
(= (describe-image $query)
   (py-call (media_handler.describe_image $query)))

(= (generate-image $prompt)
   (py-call (media_handler.generate_and_send $prompt)))
```

Skills go in core because a MeTTa plugin's `loadOmegaClawPlugin` cannot currently
register skills that reach `getSkills`. Move them here once that is fixed upstream.

**3. Dependencies** are in `requirements.txt`; core's Dockerfile installs every
`plugins/*/requirements.txt` automatically.

**4. Fill in `telegram_profile.yaml`** — it ships permissive defaults that are
**not safe for production**. Set `admin_controls.admin_ids` to the Telegram user
IDs allowed to run admin commands, and `telegram.allowed_chats` to the chat IDs
the bot may operate in. Both are empty by default.

## Use

```sh
sh run.sh run.metta commchannel=telegram_media provider=OpenRouter \
    embeddingprovider=Local TG_CHAT_ID="$TG_CHAT_ID"
```

`TG_CHAT_ID` is a run argument; `TG_BOT_TOKEN` is read from the environment. Run
only one instance per bot token — a second makes Telegram reject both.

| Variable | Required | Purpose |
| --- | --- | --- |
| `TG_BOT_TOKEN` | yes | Telegram bot token |
| `ANTHROPIC_API_KEY` | for vision | Used by the default vision provider |
| `OPENROUTER_API_KEY` | for image gen + Whisper | Also the vision key if `VISION_PROVIDER=OpenRouter` |
| `OPENAI_API_KEY` | for safety checks | Moderation API; without it the ethics passes allow content through |
| `VISION_PROVIDER` | no | `Anthropic` (default) or `OpenRouter` |
| `VISION_MODEL` | no | Overrides the provider's default vision model |
| `IMAGE_PROVIDER` | no | `OpenRouter` (default, FLUX) or `OpenAI` |
| `IMAGE_MODEL` | no | Overrides the image provider's default model |

Vision defaults to Anthropic because an OpenRouter account whose data policy
excludes vision providers gets a 404 on every vision model while text and image
generation keep working.

## Tests

```sh
for f in tests/test_*.py; do python3 "$f"; done
```

Plain asserts, no framework. Network and Telegram calls are stubbed, so no
credentials are needed.
