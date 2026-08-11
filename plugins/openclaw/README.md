# OpenClaw plugin

Adds the `delegate-task-to-openclaw-agent` skill, which hands a self-contained
task to an agent running on an external [OpenClaw](https://docs.openclaw.ai)
Gateway and returns its reply.

Each delegation runs in its own OpenClaw session: the
[OpenResponses HTTP API](https://docs.openclaw.ai/gateway/openresponses-http-api)
is stateless per request and generates a fresh session key for every call, so
delegated tasks never inherit context from each other.

## Configure the OpenClaw side

### 1. Enable the OpenResponses endpoint

The endpoint is **disabled by default** and must be turned on:

```bash
# Docker install, from the OpenClaw repository root
docker compose run --rm openclaw-cli config set --batch-json \
  '[{"path":"gateway.http.endpoints.responses.enabled","value":true}]'
docker compose restart openclaw-gateway

# Native install
openclaw config set --batch-json \
  '[{"path":"gateway.http.endpoints.responses.enabled","value":true}]'
```

It is served on the Gateway's own port (`18789` by default), alongside the
WebSocket surface.

> **Why HTTP and not the Gateway WebSocket protocol?**
> On the WebSocket path a client that presents only a shared token, without a
> paired device identity, has its requested scopes cleared to an empty set, and
> every call then fails with `missing scope: operator.write (MISSING_SCOPE)`.
> Shared-secret bearer auth on the HTTP surfaces keeps the full default
> operator scope set, so no device keypair or pairing approval is needed.

### 2. Have an agent to delegate to

The target agent must exist in `openclaw.json` under `agents.entries`. The
default install provides `main`. The run uses whatever model that agent is
configured with - the plugin never overrides it.

### 3. Get the Gateway token

Token auth is the default (`gateway.auth.mode: "token"`). The value lives in
the Gateway's `.env` as `OPENCLAW_GATEWAY_TOKEN`.

## Configure the OmegaClaw side

`config/config.yaml`:

```yaml
openClawEnabled: enabled
openClawURL: "http://172.17.0.1:18789"
openClawAgent: "main"
```

`config/plugins.yaml`:

```yaml
- name: openclaw
  loader: metta
  location: "{REPO}/plugins/openclaw"
```

### The token never reaches the agent process

Unlike a bare Python deployment, the Docker image never lets the agent
process hold the Gateway token. `OMEGACLAW_OPENCLAW_TOKEN` must still be
passed through the environment, never through config files:

```bash
docker run ... -e OMEGACLAW_OPENCLAW_TOKEN="<token>" openclaw_url="<gateway URL>" ...
```

(with `scripts/omegaclaw`: `OMEGACLAW_OPENCLAW_TOKEN=<token> ./scripts/omegaclaw start -g "<gateway URL>" ...`)

but the local Nginx proxy (`proxy/nginx.conf.template`, `location /openclaw/`)
reads it *before* the agent's environment is scrubbed (see `entrypoint.sh`)
and injects the `Authorization` header itself. The plugin then always talks
to `http://localhost:8080/openclaw/...` - it never sees the raw token, the
same way the built-in LLM providers never see `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / etc. The real Gateway address you pass to `openclaw_url=`
(or `-g`) is what Nginx forwards to; `openClawURL` in `config.yaml` becomes a
plain fallback, used only when `GATEWAY_URL` isn't set (i.e. running the
Python code directly, outside this Docker image) - keep it pointed at the
same Gateway for that case to keep working.

### Choosing the Gateway URL

| OmegaClaw runs | Gateway runs | Use |
| --- | --- | --- |
| in Docker | on the host | `http://172.17.0.1:18789` (the `docker0` bridge address) |
| in Docker | in Docker, same user-defined network | `http://<gateway-container>:18789` |
| on the host | on the host | `http://127.0.0.1:18789` |
| anywhere | on another machine | `https://openclaw.example.com` or `http://10.0.0.5:18789` |

Anything but the loopback case needs the Gateway to listen beyond loopback -
see `gateway.bind` in `openclaw.json`. Across a network, prefer `https://`.

In the fallback (non-Docker) path, `ws://` and `wss://` URLs are also
accepted and rewritten to `http://` / `https://`, so a value left over from
the previous WebSocket transport keeps working. The Nginx `proxy_pass`
target does not do this rewrite, so pass a plain `http://`/`https://` URL to
`openclaw_url=` / `-g`.

## Verify

```bash
# Gateway is up
curl -fsS http://127.0.0.1:18789/healthz

# Endpoint is enabled and the token is accepted (404 = still disabled)
curl -sS http://127.0.0.1:18789/v1/responses \
  -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'x-openclaw-agent-id: main' \
  -d '{"model":"openclaw","input":"Reply with exactly: PONG"}'
```

On OmegaClaw startup the log should show
`openclaw-plugin: OpenClaw integration is enabled`, and the agent should offer
the `delegate-task-to-openclaw-agent` skill.

## Skill result

Success:

```json
{"status": "ok", "responseId": "resp_...", "reply": "..."}
```

Failure - `type` is `invalid_input` for an empty message, `gateway` otherwise:

```json
{"status": "error", "type": "gateway", "message": "HTTP 401: Unauthorized (unauthorized)"}
```

## Troubleshooting

| Error | Cause |
| --- | --- |
| `HTTP 404` | `gateway.http.endpoints.responses.enabled` is not set, or the Gateway was not restarted after setting it |
| `HTTP 401: Unauthorized` | `OMEGACLAW_OPENCLAW_TOKEN` is empty or does not match `OPENCLAW_GATEWAY_TOKEN` |
| `HTTP 400: Unknown agent '<id>'` | `openClawAgent` does not exist in `agents.entries` |
| `Cannot reach OpenClaw Gateway` | Wrong `openClawURL`, or the Gateway is bound to loopback only |
| `missing scope ... (MISSING_SCOPE)` | The request went to the WebSocket surface instead of `/v1/responses` |

A Gateway that is still booting answers `503`; the plugin retries such a
response up to `STARTUP_RETRY_ATTEMPTS` times before giving up.
