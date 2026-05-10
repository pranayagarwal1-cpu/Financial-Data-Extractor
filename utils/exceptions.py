"""
Custom exceptions for the financial statement extraction system.

Used by guardrails, validators, and agents to signal specific failure modes
that the workflow graph can route appropriately.
"""


class GuardrailError(Exception):
    """Base class for all guardrail-triggered failures."""
    pass


class CostLimitExceededError(GuardrailError):
    """Raised when a single extraction run exceeds its token or dollar budget."""
    pass


class InputValidationError(GuardrailError):
    """Raised when a PDF fails health checks (corrupted, too large, no text layer)."""
    pass


class CircuitBreakerOpenError(GuardrailError):
    """Raised when an LLM backend circuit breaker is OPEN and no fallback is available."""
    pass
