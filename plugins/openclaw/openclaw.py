"""Bridge between an OmegaClaw dynamic skill and an external OpenClaw Gateway.

Protocol reference: https://docs.openclaw.ai/gateway/openresponses-http-api
(Gateway OpenResponses HTTP API).

The endpoint is stateless per request: the Gateway generates a fresh session
key for every call unless one is supplied, which matches the "new separate
session per delegation" contract of the skill.
"""


import json
import time
import os
import requests

from config import config_get_by_key
from src.logger import get_logger


logger = get_logger(__name__)

RESPONSES_PATH = "/v1/responses"
# Required by the request schema, but it selects the agent rather than a
# model: the run uses whatever model that agent is configured with.
MODEL_ROUTE = "openclaw"
SESSION_TIMEOUT = 600.0
STARTUP_RETRY_ATTEMPTS = 3
STARTUP_RETRY_MAX_WAIT = 5.0


class OpenClawError(RuntimeError):
    """Base error raised by the OpenClaw bridge."""

    def __init__(self, msg: str = "OpenClaw Error"):
        """Store the human-readable failure description.

        Args:
            msg (str): Message shown to the agent in the skill result.
        """
        self.msg = msg
        super().__init__(msg)


class OpenClawStartupError(OpenClawError):
    """Raised when the Gateway is still booting and asks the client to retry."""

    def __init__(self, msg: str, retry_after: float):
        """Store the retry hint sent by the Gateway.

        Args:
            msg (str): Human-readable failure description.
            retry_after (float): Seconds the Gateway asked the client to wait.
        """
        self.retry_after = retry_after
        super().__init__(msg)


def _get_token() -> str:
    """Retrieves OpenClaw token from environment variables.

    Returns:
        str: The auth token.
    """
    return os.getenv("OMEGACLAW_OPENCLAW_TOKEN", "").strip()


def send(
    message: str,
    openclaw_url: str,
    openclaw_agent: str
) -> str:
    """Send one independent task to OpenClaw.

    A new OpenClaw session is created for every invocation. The returned JSON
    string is suitable for insertion into LAST_SKILL_USE_RESULTS.

    Args:
        message (str): Self-contained natural-language task.
        openclaw_url (str): The base URL of the OpenClaw Gateway.
        openclaw_agent (str): The target agent identifier.

    Returns:
        str: JSON string containing either the OpenClaw reply or an error
             formatted for the agent's context.
    """
    if not str(message).strip():
        logger.warning("Refusing to delegate an empty message")
        return json.dumps({
            "status": "error",
            "type": "invalid_input",
            "message": "message is empty"
        }, ensure_ascii=False)
    logger.info(f"Delegating a task to OpenClaw agent '{openclaw_agent}'")
    try:
        result = _send_with_startup_retry(
            message=str(message).strip(),
            openclaw_url=str(openclaw_url),
            openclaw_agent=str(openclaw_agent)
        )
    except OpenClawError as gateway_error:
        logger.exception(f"Delegation failed: {gateway_error.msg}")
        return json.dumps({
            "status": "error",
            "type": "gateway",
            "message": gateway_error.msg
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception(f"Unexpected error while delegating to OpenClaw: {exc}")
        return json.dumps({
            "status": "error",
            "type": "gateway",
            "message": str(exc)[:256]
        }, ensure_ascii=False)
    logger.info("Delegation completed")
    return result


def _send_with_startup_retry(
    message: str,
    openclaw_url: str,
    openclaw_agent: str
) -> str:
    """Run one turn, retrying while the Gateway reports it is still starting.

    A Gateway that is still booting answers `503` with an optional
    `Retry-After` hint. That is a retryable condition.

    Args:
        message (str): The task description.
        openclaw_url (str): The base URL.
        openclaw_agent (str): Target agent ID.

    Raises:
        OpenClawError: Propagated from the underlying turn once retries are
            exhausted or the failure is not retryable.

    Returns:
        str: The JSON string produced by `_send`.
    """
    last_error = None
    for attempt in range(STARTUP_RETRY_ATTEMPTS):
        try:
            return _send(
                message=message,
                openclaw_url=openclaw_url,
                openclaw_agent=openclaw_agent
            )
        except OpenClawStartupError as startup_error:
            last_error = startup_error
            if attempt == STARTUP_RETRY_ATTEMPTS - 1:
                break
            wait = min(startup_error.retry_after, STARTUP_RETRY_MAX_WAIT)
            logger.warning(
                f"OpenClaw Gateway is still starting, retrying in {wait}s "
                f"({attempt + 1}/{STARTUP_RETRY_ATTEMPTS}): {startup_error.msg}"
            )
            time.sleep(wait)
    raise last_error or OpenClawError("OpenClaw Gateway unavailable")


def _send(
    message: str,
    openclaw_url: str,
    openclaw_agent: str
) -> str:
    """Post one agent turn to the Gateway and return the result.

    The Gateway responds only once the run reaches a terminal state, so a
    single blocking request covers the whole turn.

    Args:
        message (str): The task description.
        openclaw_url (str): The base URL.
        openclaw_agent (str): Target agent ID.

    Raises:
        OpenClawStartupError: If the Gateway is still booting.
        OpenClawError: If the Gateway is unreachable, authentication fails,
            the request is rejected, or no valid reply is found.

    Returns:
        str: The extracted textual reply formatted as a JSON string.
    """
    endpoint, headers = _request_target(openclaw_url)
    headers["x-openclaw-agent-id"] = openclaw_agent
    logger.debug(f"POST {endpoint} for agent '{openclaw_agent}'")
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                url=endpoint,
                json={
                    "model": MODEL_ROUTE,
                    "input": message,
                },
                headers=headers,
                timeout=SESSION_TIMEOUT,
            )
    except requests.RequestException as request_error:
        raise OpenClawError(
            f"Cannot reach OpenClaw Gateway: {request_error}"
        )
    if not response.ok:
        raise _http_error(response)
    return _extract_reply(response.json())


def _request_target(openclaw_url: str):
    """Pick the HTTP target and headers for one delegation call.

    In Docker, `GATEWAY_URL` (the local Nginx proxy, see proxy/nginx.conf.template)
    is always set: requests go to `{GATEWAY_URL}/openclaw{RESPONSES_PATH}` and
    Nginx injects the Bearer token from its own environment, so this process
    never holds the Gateway token. Without `GATEWAY_URL` (e.g. running outside
    Docker), fall back to talking to `openclaw_url` directly with a token read
    from `OMEGACLAW_OPENCLAW_TOKEN`.

    Args:
        openclaw_url (str): The configured Gateway base URL, used only by the
            direct fallback.

    Raises:
        OpenClawError: If neither transport can authenticate, i.e. there is no
            proxy to inject the token and no token in the environment either.

    Returns:
        tuple[str, dict]: The request URL and the headers to seed (the caller
            still adds `x-openclaw-agent-id`).
    """
    gateway_url = config_get_by_key("GATEWAY_URL")
    if gateway_url:
        logger.debug(f"Using the Nginx proxy at {gateway_url} for OpenClaw")
        return f"{gateway_url.rstrip('/')}/openclaw{RESPONSES_PATH}", {}
    token = _get_token()
    if not token:
        # Sending an empty Bearer would come back as a plain 401, which is
        # indistinguishable from a wrong token. Say what is actually missing.
        raise OpenClawError(
            "No GATEWAY_URL to proxy through and OMEGACLAW_OPENCLAW_TOKEN is "
            "empty, so the request cannot be authenticated"
        )
    logger.debug("No GATEWAY_URL, calling the OpenClaw Gateway directly")
    return _endpoint(openclaw_url), {"Authorization": f"Bearer {token}"}


def _endpoint(openclaw_url: str) -> str:
    """Build the OpenResponses endpoint URL from the configured base URL.

    `ws://` and `wss://` are accepted so that a base URL left over from the
    previous WebSocket transport keeps working.

    Args:
        openclaw_url (str): The configured Gateway base URL.

    Returns:
        str: Absolute URL of the OpenResponses endpoint.
    """
    base = openclaw_url.strip().rstrip("/")
    for ws_scheme, http_scheme in (("wss://", "https://"), ("ws://", "http://")):
        if base.startswith(ws_scheme):
            base = http_scheme + base[len(ws_scheme):]
            break
    return base + RESPONSES_PATH


def _http_error(response: requests.Response) -> OpenClawError:
    """Convert a failed HTTP response into the matching bridge error.

    Rejections carry `{"error": {"message": ..., "type": ...}}`; the HTTP
    reason is used when the body is not that envelope.

    Args:
        response (requests.Response): The failed response.

    Returns:
        OpenClawError: `OpenClawStartupError` for a booting Gateway, a plain
            `OpenClawError` otherwise.
    """
    try:
        error = response.json().get("error", {})
        message = error.get("message") or response.reason
        kind = error.get("type", "")
    except ValueError as decode_error:
        logger.warning(
            f"Gateway error body is not JSON, falling back to the HTTP "
            f"reason: {decode_error}"
        )
        message, kind = response.reason, ""
    message = f"{message} ({kind})" if kind else message
    if response.status_code == 503:
        try:
            retry_after = float(response.headers.get("Retry-After", 1))
        except ValueError as retry_after_error:
            logger.warning(
                f"Unusable Retry-After header, waiting 1s: {retry_after_error}"
            )
            retry_after = 1.0
        return OpenClawStartupError(message, retry_after)
    return OpenClawError(f"HTTP {response.status_code}: {message}")


def _extract_reply(payload: dict) -> str:
    """Extract visible text from the terminal Gateway response payload.

    The reply lives in `output`, whose message items hold `output_text`
    fragments. Non-message items (reasoning, tool calls) carry no visible
    text and are skipped.

    Args:
        payload (dict): The decoded OpenResponses response body.

    Raises:
        OpenClawError: If the response contains no textual reply.

    Returns:
        str: JSON formatted string indicating success and containing the
             combined text.
    """
    texts = [
        fragment.get("text", "")
        for item in payload.get("output", [])
        if isinstance(item, dict) and item.get("type") == "message"
        for fragment in item.get("content", [])
        if isinstance(fragment, dict) and fragment.get("type") == "output_text"
    ]
    reply = "\n".join(text for text in texts if text.strip()).strip()
    if not reply:
        raise OpenClawError(
            f"OpenClaw returned no textual reply; keys={sorted(payload)}"
        )
    return json.dumps({
        "status": "ok",
        "responseId": payload.get("id"),
        "reply": reply,
    }, ensure_ascii=False)
