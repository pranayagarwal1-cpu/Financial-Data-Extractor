"""Token counting utility for cost estimation.

Uses tiktoken for Ollama models (best available approximation).
Uses actual API usage metadata for Anthropic models.
"""

import tiktoken


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count tokens in text using tiktoken.

    Args:
        text: Text to count
        model: Model name (used to select tokenizer encoding)

    Returns:
        Token count
    """
    if not text:
        return 0

    # Map common model names to tiktoken encodings
    encoding_name = _resolve_encoding(model)
    try:
        enc = tiktoken.get_encoding(encoding_name)
        return len(enc.encode(text))
    except Exception:
        # Fallback to cl100k_base (covers most modern models)
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))


def _resolve_encoding(model: str) -> str:
    """Map model name to tiktoken encoding name."""
    model_lower = model.lower()

    # o200k_base: GPT-4o, GPT-4o-mini
    if any(m in model_lower for m in ["gpt-4o", "gpt-4-o"]):
        return "o200k_base"

    # cl100k_base: GPT-4, GPT-3.5-turbo, Claude (approximation), most modern models
    if any(m in model_lower for m in [
        "gpt-4", "gpt-3.5", "claude", "qwen", "llama",
        "mistral", "mixtral", "gemma", "phi"
    ]):
        return "cl100k_base"

    # p50k_base: GPT-3 davinci, text-davinci
    if "davinci" in model_lower or "text-" in model_lower:
        return "p50k_base"

    # r50k_base: GPT-3 older models
    if "ada" in model_lower or "babbage" in model_lower or "curie" in model_lower:
        return "r50k_base"

    return "cl100k_base"
