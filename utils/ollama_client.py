"""Ollama client wrapper that creates fresh Client instances per call."""

import ollama


def chat(*, model: str, messages: list, **kwargs):
    """Call ollama.chat via a fresh Client instance.

    Using a new Client per call avoids keeping idle HTTP connections open,
    which prevents the Python process from hanging on exit.
    """
    client = ollama.Client()
    try:
        return client.chat(model=model, messages=messages, **kwargs)
    finally:
        # Close underlying httpx client if available
        if hasattr(client, "_client") and hasattr(client._client, "close"):
            client._client.close()
