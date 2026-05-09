"""Ollama client wrapper with connection reuse."""

import atexit
import ollama

# Reuse a single client per process to avoid HTTP connection setup/teardown
# on every LLM call. The atexit hook ensures the underlying httpx client is
# closed cleanly on process exit so the interpreter does not hang.
_client = ollama.Client()


def _close_client():
    if hasattr(_client, "_client") and hasattr(_client._client, "close"):
        _client._client.close()


atexit.register(_close_client)


def chat(*, model: str, messages: list, **kwargs):
    """Call ollama.chat via the shared Client instance."""
    return _client.chat(model=model, messages=messages, **kwargs)
