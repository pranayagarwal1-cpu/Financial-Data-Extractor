"""Thin wrapper around Ollama's embedding endpoint, used for CoA retrieval."""

from utils.ollama_client import embed as _ollama_embed
from config import Config


def embed_text(text: str) -> list[float]:
    """Embed a single string using the configured embedding model."""
    return _ollama_embed(model=Config.EMBEDDING_MODEL, input=text).embeddings[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings in one Ollama call."""
    return list(_ollama_embed(model=Config.EMBEDDING_MODEL, input=texts).embeddings)
