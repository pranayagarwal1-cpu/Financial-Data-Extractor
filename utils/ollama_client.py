"""Ollama client wrapper that closes HTTP connections after each call."""

import ollama


def chat(*, model: str, messages: list, **kwargs):
    """Call ollama.chat with a context-managed Client to ensure connection cleanup."""
    with ollama.Client() as client:
        return client.chat(model=model, messages=messages, **kwargs)
