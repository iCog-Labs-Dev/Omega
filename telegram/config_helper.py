import yaml
import os
import json
import logging
import re
import openai
import asyncio

_config_cache = None
_config_mtime = 0
# The shipped profile. A host may mount its own and name it in TG_PROFILE_PATH,
# which the channel reads; the ethics categories and spam limits here have to
# come from the same file, or a host profile would govern only half the bot.
SHIPPED_PROFILE = os.path.join(os.path.dirname(__file__), "telegram_profile.yaml")


def _profile_path():
    try:
        from config import config_get_by_key
    except ModuleNotFoundError:  # outside core, as in the standalone tests
        return SHIPPED_PROFILE
    return config_get_by_key("TG_PROFILE_PATH", SHIPPED_PROFILE)

_openai_client = None


def _get_openai_client():
    """Build the moderation client lazily so importing this module (which
    happens whenever the plugin loads, independent of which channel is
    active) does not require OPENAI_API_KEY to be set. Isolated so tests
    can stub it."""
    global _openai_client
    if _openai_client is None:
        import gateway
        base_url, api_key = gateway.upstream(
            "openai", "https://api.openai.com/v1", "OPENAI_API_KEY")
        _openai_client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
    return _openai_client

def _load_config():
    """Loads and caches the telegram profile YAML configuration."""
    global _config_cache, _config_mtime

    if _config_cache is not None:
        return _config_cache

    path = _profile_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Critical Error: Telegram profile not found at {path}")

    try:
        with open(path, "r") as f:
            _config_cache = yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Error loading {path}: {e}")
        _config_cache = {}

    return _config_cache

def is_tool_disabled(tool_name):
    """Checks if a specified tool is disabled in the configuration."""
    config = _load_config()
    return config.get("disabled_tools", {}).get(tool_name, False)

def get_blocked_ethics_categories():
    """Retrieves the list of blocked ethics categories."""
    config = _load_config()
    categories = config.get("ethics_pass", {}).get("blocked_categories", [])
    return categories

_ETHICS_QUESTION = """You are a context-aware safety classifier.

Forbidden categories:
{categories}

Classify the expression. Do NOT flag:
- jokes
- idioms
- metaphors
- sarcasm
- figures of speech
- fictional/hypothetical non-actionable language

Flag only if the text clearly and literally expresses, requests, encourages,
or provides actionable content in a forbidden category."""

_MEMORY_QUESTION = """You screen notes a public chat bot wants to keep in its long-term memory.

Forbidden categories:
{categories}

Flag the note if keeping it would retain information in a forbidden category:
for example a password, API key, token or other secret, a credential, or
sensitive personal data about someone. A preference, a time zone, a chat norm
or a fact about a public topic is fine."""

_ANSWER_FORMAT = """
Return JSON ONLY:
{{
"violates": true/false,
"confidence": 0.0-1.0,
"reason": "short reason",
"is_joke_or_figure_of_speech": true/false
}}

Expression: {text!r}
"""


async def _classify_with_model(text, categories, question):
    """Asks the classification model whether text falls in one of categories.
    Raises when the model cannot be reached or answers in another shape; the
    caller decides what an unanswered question means."""
    config = _load_config()
    model_name = config.get("classification_model", {}).get("name", "gpt-4o-mini")
    prompt = (question.format(categories=categories)
              + _ANSWER_FORMAT.format(text=text))
    # Through the gateway: core scrubs OPENAI_API_KEY from the agent's
    # environment in production, so a client built from it has no key.
    response = await _get_openai_client().chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=120,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return (
        data["violates"] is True
        and data["confidence"] >= 0.80
        and not data.get("is_joke_or_figure_of_speech", False)
    )


async def use_model(text, categories):
    """Uses an LLM to classify if text violates the provided categories.
    An error lets the text through, as the ethics pass always has."""
    try:
        return await _classify_with_model(text, categories, _ETHICS_QUESTION)
    except Exception as e:
        logging.error(f"OpenAI classification error: {e}")
        return False

async def _llm_classify(text, categories):
    """Classifies text using OpenAI's Moderation API or falls back to standard LLM classification."""
    if not categories or not text.strip():
        return False

    try:
        response = await _get_openai_client().moderations.create(input=text)
        result = response.results[0]

        thresholds = {
            "violence": 0.85,
            "harassment": 0.85,
            "hate": 0.85,
            "self-harm": 0.70,
            "sexual/minors": 0.30,
        }

        for category, threshold in thresholds.items():
            if result.category_scores.model_dump().get(category, 0) >= threshold:
                return True

        return False

    except Exception as e:
        logging.error(f"OpenAI moderation error: {e}")
        logging.info(f"Opting to model usage for classification...")
        return await use_model(text, categories)

async def is_category_blocked(text):
    """Checks if the text violates any blocked ethics categories."""
    config = _load_config()
    blocked = config.get("ethics_pass", {}).get("blocked_categories", [])
    return await _llm_classify(text, blocked)


def get_forbidden_memory_categories():
    """The categories the bot must not keep in long-term memory."""
    config = _load_config()
    durable = (config.get("internal_learning") or {}).get("durable_memory") or {}
    return durable.get("categories_forbidden") or []


async def is_memory_forbidden(text):
    """True when text falls in a category the profile forbids keeping.

    An error refuses the note: a memory refused by mistake costs the bot one
    note and the model is told why, while a credential let through is kept
    and read back into every later prompt."""
    if not text.strip():
        return False
    forbidden = get_forbidden_memory_categories()
    if not forbidden:
        # capabilities.problems refuses a profile granting remember without
        # this list, so the channel never starts in this state.
        logging.error("No forbidden memory categories configured; refusing the note")
        return True
    try:
        return await _classify_with_model(text, forbidden, _MEMORY_QUESTION)
    except Exception as e:
        logging.error(f"Memory classification failed, refusing the note: {e}")
        return True


def get_spam_protection_config():
    """Retrieves spam protection thresholds from the configuration."""
    config = _load_config()
    spam_config = config.get("spam_protection", {})
    return {
        "time_window": spam_config.get("time_window", 10),
        "message_limit": spam_config.get("message_limit", 5),
        "cooldown_duration": spam_config.get("cooldown_duration", 120),
        "admin_alert_threshold": spam_config.get("admin_alert_threshold", 3)
    }
