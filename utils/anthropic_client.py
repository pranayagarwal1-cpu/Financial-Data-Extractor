"""Anthropic client wrapper with connection reuse."""

import os
import atexit

_api_key = os.getenv("ANTHROPIC_API_KEY")
_client = None


def _get_client():
    """Lazy-load Anthropic client to avoid import-time dependency."""
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic(api_key=_api_key)
    return _client


def _close_client():
    if _client is not None:
        _client.close()


atexit.register(_close_client)


def chat(*, model: str, messages: list, **kwargs):
    """Call Anthropic Messages API and return normalized response.

    Normalizes the Anthropic Message object to a dict so callers can use:
        response["message"]["content"]
    regardless of backend.
    """
    client = _get_client()
    max_tokens = kwargs.pop("max_tokens", 4096)
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
    return {"message": {"content": text}}
