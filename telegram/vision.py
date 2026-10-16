import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisionProvider:
    """One service that can caption images, and how to reach it."""
    name: str
    route: str
    base_url: str
    key_env: str
    default_model: str


# The vision provider is chosen INDEPENDENTLY of the chat `provider`: not every
# chat provider can see images, and an account's data policy can exclude the
# ones that can. Every entry speaks the OpenAI chat-completions dialect
# (Anthropic serves one at /v1/), so a single client shape covers them all and
# switching is a matter of base URL, key and model.
#
# The order is the order they are tried in. OpenRouter comes first because the
# bot already needs its key for chat, image generation and transcription; an
# account whose data policy excludes vision providers gets a 404 there, and
# then the image goes to Anthropic instead.
VISION_PROVIDERS = (
    VisionProvider(
        name="OpenRouter",
        route="openrouter",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        default_model="anthropic/claude-haiku-4.5",
    ),
    VisionProvider(
        name="Anthropic",
        route="anthropic",
        base_url="https://api.anthropic.com/v1/",
        key_env="ANTHROPIC_API_KEY",
        default_model="claude-haiku-4-5-20251001",
    ),
)
PROVIDERS_BY_NAME = {p.name: p for p in VISION_PROVIDERS}

# Longest provider error kept in the failure text the agent sees.
ERROR_LIMIT = 300


def _setting(key):
    """Read one setting the way the rest of the agent does.

    Inside the container the entrypoint clears the environment, so a setting
    only arrives as a command line argument (`./run-plugin --arg KEY=value`),
    which core's config reads. Outside the container a plain environment
    variable works too."""
    value = None
    try:
        from config import config_get_by_key
        value = config_get_by_key(key, None)
    except ModuleNotFoundError:
        pass
    value = value or os.environ.get(key)
    return str(value).strip() if value else ""


def _provider_order():
    """The providers to try, in order.

    VISION_PROVIDER moves one provider to the front; the others stay behind it
    as fallbacks."""
    chosen = _setting("VISION_PROVIDER")
    if not chosen:
        return list(VISION_PROVIDERS)
    first = PROVIDERS_BY_NAME.get(chosen)
    if first is None:
        raise RuntimeError(
            f"unknown VISION_PROVIDER {chosen!r}; expected one of {sorted(PROVIDERS_BY_NAME)}")
    return [first] + [p for p in VISION_PROVIDERS if p is not first]


def _model(provider, is_first):
    """VISION_MODEL applies to the first provider only, since a model name
    rarely means the same thing on another service."""
    override = _setting("VISION_MODEL") if is_first else ""
    return override or provider.default_model


def _make_client(provider):
    """Create the client for one provider. Isolated so tests can stub it.

    Behind the gateway proxy there is no key to read - the proxy holds it - so
    the destination comes from gateway.upstream rather than the environment."""
    import openai
    import gateway
    base_url, api_key = gateway.upstream(provider.route, provider.base_url, provider.key_env)
    if not api_key:
        raise RuntimeError(
            f"not available (set {provider.key_env}, or run behind the gateway proxy)")
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def _describe_error(error):
    """Short text for one provider's failure, with the HTTP status when there is one."""
    status = getattr(error, "status_code", None)
    text = str(error).strip() or type(error).__name__
    if len(text) > ERROR_LIMIT:
        text = text[:ERROR_LIMIT] + "..."
    return f"HTTP {status}: {text}" if status else text


def _caption_with(provider, model, image_parts, prompt, max_tokens):
    """Ask one provider for a caption; raise on any failure or an empty answer."""
    client = _make_client(provider)
    content = [{"type": "text", "text": prompt}] + list(image_parts)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    caption = (resp.choices[0].message.content or "").strip()
    if not caption:
        raise RuntimeError("empty caption")
    return caption


def vision_chat(image_parts, prompt, max_tokens=1024):
    """Caption image(s), trying each vision provider until one answers.

    OpenRouter is tried first and Anthropic second, so a bot whose OpenRouter
    account cannot see images, or whose Anthropic key is missing, still gets a
    caption from the other one. VISION_PROVIDER picks which goes first and
    VISION_MODEL replaces that provider's model:

        ./run-plugin --arg VISION_PROVIDER=Anthropic

    `image_parts` are OpenAI multimodal `image_url` parts, which is what the
    telegram channel already produces (one base64 `data:` URI per attached
    image), so no per-provider reshaping is needed. Returns the caption text.
    When every provider fails it raises one error naming each provider and why
    it failed, such as "OpenRouter: HTTP 404: ...; Anthropic: HTTP 401: ...",
    and `media_handler.describe_image` turns that into the failure the agent
    sees.

    `max_tokens` is always sent because Anthropic's compatibility endpoint
    requires it; the default is ample for a caption.
    """
    failures = []
    for index, provider in enumerate(_provider_order()):
        model = _model(provider, is_first=index == 0)
        try:
            caption = _caption_with(provider, model, image_parts, prompt, max_tokens)
        except Exception as e:
            reason = _describe_error(e)
            logger.warning("Vision provider %s (%s) failed: %s", provider.name, model, reason)
            failures.append(f"{provider.name}: {reason}")
            continue
        logger.info("Vision described image with %s (%s): %d chars",
                    provider.name, model, len(caption))
        return caption
    raise RuntimeError("no vision provider could describe the image; " + "; ".join(failures))


def loadOmegaPlugin():
    """Plugin entry point. Importing this module puts `plugins/telegram` on sys.path
    and imports media_handler, so `py-call (media_handler.describe_image ...)` in
    skills.metta resolves. describe_image calls vision_chat directly, so no
    LLMProvider needs registering for the describe-image path."""
    import media_handler  # noqa: F401 — ensures the media_handler alias is set
