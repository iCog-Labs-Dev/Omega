# omega-telegram

A Telegram communication channel for Omega with media support: the agent can
read images, PDFs and voice notes that users attach, generate images and PDFs,
and send voice replies when enabled.

This is the `telegram` channel. It replaces core's earlier HTTP-polling Telegram
channel and keeps everything that one provided — the gateway proxy path, the
outbound retry queue, and the channel auth handshake.

## Capabilities

| Capability | How the agent sees it |
| --- | --- |
| Inbound photo | Message shows `[image]`; the agent calls `describe-image` on demand |
| Inbound QR code | `describe-image` decodes it and returns the contents fenced as untrusted text; the agent relays them without acting on them |
| Inbound PDF | Extracted text is inlined into the message |
| Inbound voice / audio | Whisper transcript is inlined into the message |
| Outbound image | The agent calls `generate-image`, which generates and sends the photo |
| Outbound PDF | The agent calls `generate-pdf`, which creates a local PDF and sends it as a Telegram document (up to 20,000 characters). Hebrew and Arabic are shaped; text with characters no font can draw is refused and the characters are named to the agent. See [PDF fonts](#pdf-fonts) |
| Outbound voice | The agent calls `speak`, which synthesizes and sends a Telegram voice message when enabled |
| Admin commands | `/kill`, `/pause [chat_id]`, `/togglesearch`, `/purge` (admin IDs only) |
| Safety | Ethics classification on inbound and outbound text, per-user spam throttling |
| Scope limits | `prompt.txt` is added to the agent's prompt as its own section |
| Authorization | `telegram_profile.yaml` chat/DM rules, plus core's auth handshake when enabled |
| Delivery | Outbound messages queue until the bot connects, and are retried on failure |

## Install

**1. Register the plugin** in core's `config/plugins.yaml`:

```yaml
- name: telegram
  loader: metta
  location: "{REPO}/plugins/telegram"
```

The `metta` loader is what lets `telegram.metta` register the plugin's skills and
its prompt section at load time. Core needs no telegram-specific code: the two
media skills are added with `add-skill`, not written into `src/skills.metta`.

**2. Dependencies** are in `requirements.txt`; core's Dockerfile installs every
`plugins/*/requirements.txt` automatically.

**3. Point at your own config if you want to** — `TG_PROFILE_PATH` and
`TG_POLICY_PATH` override where the plugin reads `telegram_profile.yaml` and
`policy.md`, resolved by core from the command line, an `OMEGA_`-prefixed
environment variable, or `config.yaml`. The files shipped here are the defaults;
mounting over them works too and needs no configuration.

**4. Fill in `telegram_profile.yaml`** — it ships permissive defaults that are
**not safe for production**. Set `admin_controls.admin_ids` to the Telegram user
IDs allowed to run admin commands, and `telegram.allowed_chats` to the chat IDs
the bot may operate in. Both are empty by default. Voice replies are opt-in;
PDF generation is enabled by default for compatibility with existing profiles;
set `telegram.reply_constraints.allow_pdf_generation: false` to disable it.
set `telegram.reply_constraints.allow_voice_reply: true` to enable them.

## Use

```sh
sh run.sh run.metta commchannel=telegram provider=OpenRouter \
    embeddingprovider=Local TG_CHAT_ID="$TG_CHAT_ID"
```

`TG_CHAT_ID` is a run argument; `TG_BOT_TOKEN` is read from the environment. Run
only one instance per bot token — a second makes Telegram reject both.

When `GATEWAY_URL` is set every outbound call goes to that proxy, which holds the
credentials, and none of the keys below need to be in the agent's environment —
the container entrypoint scrubs them precisely so they are not. Telegram uses
two routes, `/telegram/` for API methods and `/telegram-file/` for downloads,
because Telegram serves files from a different path prefix. Vision, image
generation, transcription and moderation use `/anthropic/`, `/openrouter/` and
`/openai/`.

The keys in the table below are what a **direct** run needs. Behind the proxy
they belong to the proxy, not here.

| Variable | Required | Purpose |
| --- | --- | --- |
| `TG_BOT_TOKEN` | yes | Telegram bot token |
| `ANTHROPIC_API_KEY` | for vision | Used by the default vision provider |
| `OPENROUTER_API_KEY` | for image gen + Whisper | Also the vision key if `VISION_PROVIDER=OpenRouter` |
| `OPENAI_API_KEY` | for safety checks | Moderation API; without it the ethics passes allow content through |
| `EDGE_TTS_VOICE` | no | Voice for the `speak` skill; any voice from `edge-tts --list-voices`, defaults to `en-US-AriaNeural` |
| `VISION_PROVIDER` | no | `Anthropic` (default) or `OpenRouter` |
| `VISION_MODEL` | no | Overrides the provider's default vision model |
| `IMAGE_PROVIDER` | no | `OpenRouter` (default, FLUX) or `OpenAI` |
| `IMAGE_MODEL` | no | Overrides the image provider's default model |
| `TG_PROFILE_PATH` | no | Path to the channel profile, defaults to the shipped one |
| `TG_POLICY_PATH` | no | Path to the user-facing policy text, defaults to the shipped one |
| `TG_PROMPT_PATH` | no | Path to the prompt section, defaults to the shipped one |
| `TG_PDF_FONT_DIR` | no | Folder PDF fonts are installed to and read from, defaults to `memory/fonts`; see [PDF fonts](#pdf-fonts) |

Vision defaults to Anthropic because an OpenRouter account whose data policy
excludes vision providers gets a 404 on every vision model while text and image
generation keep working.

## PDF fonts

PDFs are drawn with the bundled DejaVu Sans, which covers Latin, Greek,
Cyrillic, Hebrew and Arabic. When a PDF needs another script, `generate-pdf`
refuses it and names the characters, and the agent can call `install-pdf-font`
with a Google Fonts family to add one:

```
install-pdf-font Noto Sans Devanagari
```

The skill only downloads from the OFL folder of
[google/fonts](https://github.com/google/fonts/tree/main/ofl), by family name.
The agent never passes a URL, redirects are not followed, a font may be at most
32 MB and the folder 200 MB. Variable fonts are saved at Regular weight, since
fpdf2 would draw them at their default, which for Noto Sans SC is Thin. Fonts
PDFs cannot use are refused: CFF fonts, which fpdf2 embeds under the wrong
font type, and colour emoji fonts, whose bitmaps fpdf2 cannot draw (Noto Emoji
works). Downloads run in the background: the user is told the fonts for their
request are downloading, and when it ends the agent gets the result as a new
message in that chat and sends the PDF. Noto Sans SC takes about 20 seconds;
most families take a few.

Fonts go to `fonts/` in core's memory folder, so they survive restarts.
`TG_PDF_FONT_DIR` points somewhere else, and copying `.ttf` files into the folder
pre-installs them. Each character is drawn with DejaVu Sans when it can be, and
otherwise with the first font in the folder, by filename, that has it, so an
installed font can add scripts but never change how the bundled ones look. The
folder is read each time a PDF is made, and the fonts in use are logged at
startup. Set `telegram.reply_constraints.allow_pdf_font_install: false` to stop
the agent installing fonts.

One known gap is a line that mixes Hebrew and Arabic: fpdf2 shapes it as one
run, so the Arabic letters are not joined and the two words come out in the
wrong order. Either language alone, or mixed with English, is fine.

## Location

The plugin does not have to live inside the core tree. It finds its own files
relative to its module, and reaches core's Python only through modules core puts
on the path, so `location` in `plugins.yaml` can be any absolute path:

```yaml
- name: telegram
  loader: metta
  location: "/opt/omega-telegram"
```

Verified by loading it from outside the repository with the in-tree copy removed.

## Tests

```sh
for f in tests/test_*.py; do python3 "$f"; done
```

Plain asserts, no framework. Network and Telegram calls are stubbed, so no
credentials are needed.
