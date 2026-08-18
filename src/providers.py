import logging

logger = logging.getLogger(__name__)

_llmProviderRegistry = {}

class LLMProvider:
    """LLM provider implementation"""

    def start(self) -> None:
        """Configure and start LLM provider"""
        raise NotImplementedError()

    def stop(self) -> None:
        """Stop and LLM provider and free resources"""
        raise NotImplementedError()

    def chat(self, prompt: str, max_tokens: int = 6000, reasoning_mode: str = "medium") -> str:
        """Chat with LLM provider"""
        raise NotImplementedError()

def registerLLMProvider(id: str, provider: LLMProvider) -> None:
    """Register LLM provider in the registry"""
    global _llmProviderRegistry
    logger.info(f"registerLLMProvider: registering LLM provider {id}")
    _llmProviderRegistry[id] = provider

_llmprovider: LLMProvider = None

def llmProviderStart(provider):
    """Select and start one of the LLM providers registered by plugins"""
    global _llmprovider
    _llmprovider = _llmProviderRegistry.get(provider, None)
    if _llmprovider is None:
        _error("llmProviderStart", f"LLM provider plugin {provider} is not registered")
    _llmprovider.start()

def llmProviderChat(prompt, max_tokens, reasoning_mode):
    """Chat via selected LLM provider, notifying the user on quota errors."""
    global _llmprovider
    try:
        result = _llmprovider.chat(prompt, max_tokens, reasoning_mode)
        # Reset the quota-notified flag on any successful response
        _quota_error_notified.clear()
        return result
    except Exception as e:
        from lib_llm_ext import LLMQuotaExceededError
        if isinstance(e, LLMQuotaExceededError):
            logger.error(f"[llmProviderChat] LLM quota exceeded, notifying user: {e}")
            # Only notify once per unique error message until a success clears it
            err_key = str(e)
            if err_key not in _quota_error_notified:
                _quota_error_notified.add(err_key)
                _notify_user(str(e))
        else:
            logger.exception(f"[llmProviderChat] Unexpected error from LLM provider: {e}")
        return ""

# Tracks quota errors already notified so we don't spam the user
_quota_error_notified: set = set()

def _notify_user(message: str) -> None:
    """Send an error message to the user via the active communication channel."""
    try:
        # Must import as 'channels' (not 'src.channels') — that is the module
        # instance where commChannelStart() registers _commchannel.
        import channels
        channels.commChannelSend(message)
    except Exception as e:
        logger.error(f"[llmProviderChat] Failed to notify user of LLM error: {e}")
