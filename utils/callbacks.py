"""
Timing utilities for observability.

Since we use raw ollama.chat calls (not LangChain), we manually instrument
nodes and LLM calls with simple timing wrappers.
"""

import time
from typing import Callable, Any, Optional
from functools import wraps


class Timer:
    """Simple context manager for timing code blocks."""

    def __init__(self):
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration_ms: float = 0.0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000


def timed_llm_call(func: Callable) -> Callable:
    """
    Decorator to time LLM calls and log to observability.

    Usage:
        @timed_llm_call
        def my_llm_function(model, messages, ...):
            ...
    """
    from .observability import get_observability

    @wraps(func)
    def wrapper(*args, **kwargs):
        obs = get_observability()
        run_id = kwargs.get('run_id')

        with Timer() as timer:
            result = func(*args, **kwargs)

        # Extract model from kwargs or args
        model = kwargs.get('model', args[0] if args else 'unknown')

        # Extract prompt from messages
        messages = kwargs.get('messages', args[1] if len(args) > 1 else [])
        prompt = messages[0].get('content', '') if messages else ''

        # Extract response content
        response = ''
        if isinstance(result, dict):
            response = result.get('message', {}).get('content', '')

        obs.log_llm_call(
            model=model,
            duration_ms=timer.duration_ms,
            prompt=prompt,
            response=response,
            run_id=run_id
        )

        return result

    return wrapper
