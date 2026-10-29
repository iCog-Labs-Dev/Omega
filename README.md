# SNET TELEGRAM BOT

A Telegram bot with media support. It reads images, QR codes, PDFs and voice
notes that users attach, and can send back generated images, PDF documents and
voice replies.

Everything ships as an Omega plugin in `telegram/`. `telegram/telegram.metta`
registers the Telegram `CommChannel` and the skills `describe-image`,
`generate-image`, `generate-pdf`, `install-pdf-font` and `speak`. Core is never
edited: `./run-plugin` copies the plugin into a core checkout, and that copy is
what gets built and run.

## Where the code lives

| Repository | Branch | Role |
| --- | --- | --- |
| `iCog-Labs-Dev/Omega` | `telegram-plugin` | Where all work happens. Open pull requests here. |
| `singnet/Omega_CustomBots` | `SNET_Telegram` | Deploys only. It holds the deploy secrets and the release tags. |

`SNET_Telegram` should always hold the same files as `telegram-plugin`. Do not
commit to it directly: a change made only there is lost the next time it is
synced. To release, sync first:

```sh
git fetch icog && git fetch cbots
git checkout -B sync cbots/SNET_Telegram
git read-tree -u --reset icog/telegram-plugin   # take telegram-plugin's files exactly
git commit -m "Sync with telegram-plugin at $(git rev-parse --short icog/telegram-plugin)"
git push cbots sync                              # then open a PR into SNET_Telegram
```

Here `icog` is `git@github.com:iCog-Labs-Dev/Omega.git` and `cbots` is
`https://github.com/singnet/Omega_CustomBots.git`.

## Capabilities

| Capability | How the agent sees it |
| --- | --- |
| Inbound photo | Message shows `[image]`; the agent calls `describe-image` on demand, which also reads any QR code in it |
| Inbound PDF | Extracted text is inlined into the message |
| Inbound voice / audio | Whisper transcript is inlined into the message |
| Outbound image | `generate-image` generates and sends the photo |
| Outbound PDF | `generate-pdf` builds the document locally and sends it; `install-pdf-font` adds a Google Fonts family for scripts the bundled font cannot draw |
| Outbound voice | `speak` sends speech as ordered voice messages, picking a voice for the language, when `allow_voice_reply` is on |
| Admin commands | `/kill`, `/pause [chat_id]`, `/togglesearch`, `/purge` (admin IDs only) |
| Safety | Ethics classification on inbound and outbound text, per-user spam throttling, link targets written out in replies, secrets and card numbers refused in PDFs |

## Running it locally

1. Put the keys in `.env`:
   - `TG_BOT_TOKEN`, from @BotFather
   - `OPENROUTER_API_KEY`, for chat, reading images, image generation and voice transcription
   - `ANTHROPIC_API_KEY`, optional, for reading images when OpenRouter cannot
   - `OPENAI_API_KEY`, for the Moderation API used by the safety checks
2. Edit `telegram/telegram_profile.yaml` and fill in both of these. They ship
   empty, and the bot is not safe to run in a real chat until they are set:
   - `admin_controls.admin_ids`, the Telegram user IDs allowed to run admin commands
   - `telegram.allowed_chats`, the chat IDs the bot may answer in
3. In BotFather, turn **Group Privacy off** for the bot. Otherwise it cannot see
   group messages that are not direct replies to it.
4. `./run-plugin --arg TG_CHAT_ID=<chat id>`

The first run builds core from source, which takes about ten minutes. Later runs
reuse the image. `./run-plugin --help` lists the options, including `--test` to
run the suite inside the built image and `--shell` to open a shell in it.

Run only one instance per bot token. A second one makes Telegram reject both
with `TelegramConflictError`.

## How the bot runs

The container starts under core's own entrypoint. It brings up core's
credential proxy while the keys are still in the environment, removes them from
the environment the agent sees, and switches to uid 65534 (`nobody`) before the
agent starts. The plugin reaches Telegram, OpenRouter, Anthropic and OpenAI
through that proxy, so the agent, and its `shell` skill, never see a key.

Core is built from `CORE_REMOTE` at `CORE_REF`, which default to the `telegram`
branch of `iCog-Labs-Dev/Omega`. Against a core that lacks something the plugin
needs, `run-plugin` adds it to its copy: the manifest entry, the plugin's
requirements install, and removing core's own `channels/telegram.py`, which
claims the same channel id.

## Configuration

Behaviour lives in `telegram/telegram_profile.yaml`: reply gating, admin IDs,
spam thresholds, ethics categories, and `reply_constraints`, which turns
features on and off (`allow_image_generation`, `allow_pdf_generation`,
`allow_pdf_font_install`, `allow_voice_reply`). `callable_capabilities` is the
allowlist of skills the model may call; every other skill, the shell and file
I/O included, is refused. `internal_learning.durable_memory.categories_forbidden`
lists what `remember` may not keep (secrets, credentials, sensitive personal
data and so on); each note is checked against it by the classification model
and refused if it falls in one, or if the check fails. The bot will not start on a profile holding a key or
constraint the plugin does not act on, and the error names each one, so a host
profile written for an older release has to be updated with it.
`telegram/policy.md` holds the `/start`, `/about` and `/privacy` text.

Optional settings:

| Variable | Purpose |
| --- | --- |
| `VISION_PROVIDER` | Which vision provider to try first: `OpenRouter` (default) or `Anthropic` |
| `VISION_MODEL` | Overrides the model of the provider tried first |
| `IMAGE_PROVIDER` | `OpenRouter` (default, FLUX) or `OpenAI` |
| `IMAGE_MODEL` | Overrides the image provider's default model |
| `EDGE_TTS_VOICE` | Fixes the speech voice instead of choosing one by language |
| `TG_PDF_FONT_DIR` | Where installed PDF fonts go; defaults to `fonts/` in core's memory folder, which survives restarts |
| `TG_PROFILE_PATH`, `TG_POLICY_PATH`, `TG_PROMPT_PATH` | Use a profile, policy or prompt file from outside the plugin |

Vision tries OpenRouter first and falls back to Anthropic. An OpenRouter
account whose data policy excludes vision providers gets a 404 on every vision
model, while text chat and image generation keep working on the same key; the
image then goes to Anthropic. Pass these settings with `--arg`, for example
`./run-plugin --arg VISION_PROVIDER=Anthropic`, because the container does not
pass environment variables through to the bot.

## Tests

```sh
pip install pytest -r telegram/requirements.txt
pytest telegram/tests -q
```

Network and Telegram calls are stubbed, so no credentials are needed, and core
does not have to be installed. The live suite in `telegram/tests/test_e2e.py`
skips itself unless `TG_E2E=1` is set. It talks to a real bot, so give it an
idle token, not one a running bot is polling with.

## Continuous delivery

| Trigger | Workflow | What happens |
| --- | --- | --- |
| pull request to `telegram-plugin` or `SNET_Telegram` | `ci.yml` | test, build, smoke test; publishes nothing |
| `pre-prod-*` tag on `singnet/Omega_CustomBots` | `deploy-staging.yml` | the same, then publishes the image and deploys to staging |
| `prod-*` tag on `singnet/Omega_CustomBots` | `deploy-production.yml` | the same against production, with `--restart=always` |

The deploy workflows do nothing in any other repository, so a tag pushed to
`iCog-Labs-Dev/Omega` deploys nothing. `common.yml` builds the image and
`deploy.yml` puts it on a server; the other workflows call them.

### What has to pass

The plugin's test suite runs on the runner. Then the built image loads the
plugin the way core will: it reads the manifest through core's own loader and
imports every plugin module. The suite stubs core out and cannot catch a
missing dependency; only the image can.

### Releasing

1. On `telegram-plugin`, add a `## prod-YYYY-MM-DD` section to `CHANGELOG.md`
   describing what people in the chat will notice.
2. Sync `SNET_Telegram` as above and merge the pull request.
3. Publish the notes. The bot reads them, without logging in, from the GitHub
   release of its tag in `iCog-Labs-Dev/Omega`, because `Omega_CustomBots` is
   private. Tag `telegram-plugin` there and publish before deploying, or the
   bot starts with nothing to announce:

   ```sh
   tag=pre-prod-YYYY-MM-DD   # prod-YYYY-MM-DD for production
   git tag "$tag" icog/telegram-plugin && git push icog "$tag"
   python3 scripts/changelog-section.py "$tag" \
     | gh release create "$tag" --repo iCog-Labs-Dev/Omega --title "$tag" \
         --notes-file - --verify-tag --prerelease   # no --prerelease for prod-*
   ```

4. Tag the merged `SNET_Telegram` commit `pre-prod-YYYY-MM-DD` and push the tag
   to `singnet/Omega_CustomBots`. Staging deploys and announces that section.
   Add `.2`, `.3` and so on for another rehearsal of the same notes.
5. Repeat steps 3 and 4 with `prod-YYYY-MM-DD`. Production deploys.

Production refuses to build unless the `CORE_REF` repository variable is a full
40-character commit, so a branch moving mid-release cannot change what ships:

```sh
gh variable set CORE_REF --repo singnet/Omega_CustomBots --body <sha>
```

Staging builds from the same variable.

Images go to `ghcr.io/singnet/omega-telegram-snet`, tagged `staging-<sha>` or
`production-<sha>`. The deploy keeps the three newest on the server.

### On the server

The deploy reads the keys from Infisical (`staging` or `prod`), connects over
SSH, and replaces the container named `omega`. Memory lives in
`/Backup/docker/PeTTa`. If `~/omega/telegram_profile.yaml` exists on the host it
is mounted in place of the shipped profile, and it must be readable by
everyone, since the agent runs as `nobody`. Production refuses to deploy
without a chat id: the shipped profile lists no allowed chats, and an empty list
restricts nothing.

The repository needs nine secrets, which `./push-cicd-secrets` sets from a filled
`.env.cicd` (it needs admin on the repository):

| Secret | |
| --- | --- |
| `INFISICAL_PROJECT_SLUG` | the project holding the keys, shared |
| `INFISICAL_CLIENT_ID_STAGING`, `INFISICAL_CLIENT_SECRET_STAGING` | the staging machine identity |
| `INFISICAL_CLIENT_ID_PRODUCTION`, `INFISICAL_CLIENT_SECRET_PRODUCTION` | the production machine identity |
| `SSH_HOST_STAGING`, `SSH_HOST_PRODUCTION` | the two servers |
| `SSH_USER`, `SSH_PRIVATE_KEY` | shared by both |

Optional repository variables: `LLM_PROVIDER` (default `OpenRouter`),
`LLM_MODEL`, `CORE_REMOTE`, `CORE_REF`, and `RELEASE_NOTES_REPO`, which is set
to `iCog-Labs-Dev/Omega` so the deploy leaves publishing to step 3 above.

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) file for details.
