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

Guardrail integration:
    - Cost guardrail is enforced before every LLM call via ``run_id`` kwarg.
"""

import base64
import logging

from utils.ollama_client import chat as _ollama_chat
from utils.anthropic_client import chat as _anthropic_chat
from utils.token_counter import count_tokens
from config import Config
from utils.guardrails import get_guardrails
from utils.observability import _calculate_cost, _get_model_rate


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-")


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def chat(*, model: str, messages: list, run_id: str = None, **kwargs):
    """
    Unified chat interface.

    Args:
        model: Model name. Auto-routed by prefix (claude-* → Anthropic).
        messages: List of dicts with keys: role, content, images (optional).
        run_id: Optional run ID for cost-guardrail enforcement.
        **kwargs: Passed through to the backend (e.g., options, max_tokens).

    Returns:
        Dict with {"message": {"content": str}} for consistent access.
        May include {"usage": {"prompt_tokens": int, "completion_tokens": int}}.
    """
    # --- Cost guardrail check ---
    if run_id and not Config.DISABLE_GUARDRAILS:
        gr = get_guardrails()
        allowed, reason = gr.cost.check(run_id, model)
        if not allowed:
            from utils.exceptions import CostLimitExceededError
            logging.error(f"[guardrail] Cost limit exceeded for run {run_id}: {reason}")
            raise CostLimitExceededError(reason)

    if _is_anthropic_model(model):
        response = _anthropic_chat(model=model, messages=_convert_messages_for_anthropic(messages), **kwargs)
    else:
        # Ollama path — compute actual token counts since Ollama doesn't return them.
        raw = _ollama_chat(model=model, messages=messages, **kwargs)

        # ollama>=0.4 returns a Pydantic ChatResponse, not a dict. It supports
        # attribute access (raw.message.content) and rejects unknown fields, so
        # we normalize to a plain dict matching the Anthropic shape rather than
        # mutating the model.
        message = getattr(raw, "message", None)
        if message is None and isinstance(raw, dict):
            message = raw.get("message", {})
        response_text = getattr(message, "content", None)
        if response_text is None and isinstance(message, dict):
            response_text = message.get("content", "")
        response_text = response_text or ""

        prompt_text = "\n".join(str(m.get("content", "")) for m in messages)
        prompt_tokens = count_tokens(prompt_text, model)
        completion_tokens = count_tokens(response_text, model)

        response = {
            "message": {"content": response_text},
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    # --- Cost guardrail charge ---
    if run_id and response.get("usage"):
        usage = response["usage"]
        total_tokens = usage.get("total_tokens", 0)
        rate = _get_model_rate(model)
        cost = _calculate_cost(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            model,
        )
        gr = get_guardrails()
        gr.cost.charge(run_id, total_tokens, cost)

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
