"""
Unified LLM client supporting Ollama and Anthropic backends.

Usage:
    from utils.llm_client import chat
    response = chat(model="qwen3.5", messages=[...], images=["page.jpg"])
    # response is always {"message": {"content": "..."}}
    # response may include {"usage": {"prompt_tokens": N, "completion_tokens": M}}

Model routing:
    - Names starting with "claude-" → Anthropic (e.g., claude-sonnet-4-6)
    - Everything else → Ollama (e.g., qwen3.5, llama3)

Vision support:
    - Pass images=["path/to/page.jpg"] in any message dict.
    - Automatically base64-encoded for Anthropic; forwarded as-is for Ollama.
"""

import base64

from utils.ollama_client import chat as _ollama_chat
from utils.anthropic_client import chat as _anthropic_chat
from utils.token_counter import count_tokens


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-")


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def chat(*, model: str, messages: list, **kwargs):
    """
    Unified chat interface.

    Args:
        model: Model name. Auto-routed by prefix (claude-* → Anthropic).
        messages: List of dicts with keys: role, content, images (optional).
        **kwargs: Passed through to the backend (e.g., options, max_tokens).

    Returns:
        Dict with {"message": {"content": str}} for consistent access.
        May include {"usage": {"prompt_tokens": int, "completion_tokens": int}}.
    """
    if _is_anthropic_model(model):
        response = _anthropic_chat(model, _convert_messages_for_anthropic(messages), **kwargs)
        return response

    # Ollama path — compute actual token counts since Ollama doesn't return them
    response = _ollama_chat(model=model, messages=messages, **kwargs)

    # Estimate tokens from prompt + response text
    prompt_text = "\n".join(
        str(m.get("content", "")) for m in messages
    )
    response_text = response.get("message", {}).get("content", "")

    prompt_tokens = count_tokens(prompt_text, model)
    completion_tokens = count_tokens(response_text, model)

    response["usage"] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }

    return response


def _convert_messages_for_anthropic(messages: list) -> list:
    """Convert Ollama-style messages (with 'images' key) to Anthropic format."""
    converted = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        images = msg.get("images", [])

        if images:
            # Build Anthropic content blocks: text + images
            blocks = [{"type": "text", "text": content}]
            for img_path in images:
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": _encode_image(img_path),
                    }
                })
            converted.append({"role": role, "content": blocks})
        else:
            converted.append({"role": role, "content": content})

    return converted
