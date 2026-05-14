"""Anthropic client wrapper with connection reuse and usage tracking."""

import os
import atexit

_client = None


def _get_client():
    """Lazy-load Anthropic client to avoid import-time dependency."""
    global _client
    if _client is None:
        # Read the API key here, not at module import — env vars set by
        # python-dotenv, Streamlit secrets, or callers after import are
        # otherwise silently ignored and surface as cryptic auth errors.
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your environment "
                "or .env file before calling an Anthropic model."
            )
        from anthropic import Anthropic
        _client = Anthropic(api_key=api_key)
    return _client


def _close_client():
    if _client is not None:
        _client.close()


atexit.register(_close_client)


def chat(*, model: str, messages: list, **kwargs):
    """Call Anthropic Messages API and return normalized response with usage.

    Normalizes the Anthropic Message object to a dict so callers can use:
        response["message"]["content"]
    regardless of backend.

    Also includes usage metadata:
        response["usage"]["prompt_tokens"]
        response["usage"]["completion_tokens"]
        response["usage"]["total_tokens"]
    """
    client = _get_client()
    max_tokens = kwargs.pop("max_tokens", 16384)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=messages,
        **kwargs
    )
    text = ""
    if response.content:
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text += block.text

    usage = getattr(response, "usage", None)
    usage_dict = {}
    if usage:
        usage_dict = {
            "prompt_tokens": getattr(usage, "input_tokens", 0),
            "completion_tokens": getattr(usage, "output_tokens", 0),
            "total_tokens": getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0),
        }

    return {
        "message": {"content": text},
        "usage": usage_dict,
    }
