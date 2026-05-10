"""
Guardrail layer for the financial statement extraction system.

Provides:
- CostGuardrail: per-run token/cost budget enforcement
- QualityTracker: sliding-window evaluation-score degradation detection
- InputValidator: PDF health checks before LLM calls
- ConcurrencyLimiter: caps parallel workflow invocations
- GuardrailRegistry: singleton orchestrator

Usage:
    from utils.guardrails import get_guardrails
    gr = get_guardrails()
    gr.input.validate(pdf_path)
    gr.cost.check(run_id, model)
    gr.cost.charge(run_id, tokens, cost)
    gr.quality.record(run_id, avg_score)
    gr.concurrency.acquire()
    try:
        ...
    finally:
        gr.concurrency.release()
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import Config
from utils.exceptions import CostLimitExceededError, InputValidationError


# ---------------------------------------------------------------------------
# Cost Guardrail
# ---------------------------------------------------------------------------

@dataclass
class _CostRunState:
    """Mutable per-run accumulator (not thread-safe by itself)."""
    tokens_used: int = 0
    cost_used: float = 0.0
    warned_80: bool = False


class CostGuardrail:
    """
    Per-run token and dollar budget accumulator.

    Warns at 80% of budget, hard-stops at 100%.
    Purges run state on ``end_run()`` to prevent memory leaks.
    """

    def __init__(self):
        self._runs: Dict[str, _CostRunState] = {}
        self._lock = threading.Lock()

    def check(self, run_id: str, model: str) -> Tuple[bool, Optional[str]]:
        """
        Check whether another LLM call is allowed for this run.

        Returns:
            (allowed, reason_or_none)
        """
        if not run_id:
            return True, None  # No budget tracking without run_id

        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return True, None

            # Warn at 80%
            if not state.warned_80:
                token_ratio = state.tokens_used / max(Config.GUARDRAIL_MAX_TOKENS_PER_RUN, 1)
                cost_ratio = state.cost_used / max(Config.GUARDRAIL_MAX_COST_PER_RUN, 0.01)
                if token_ratio >= 0.8 or cost_ratio >= 0.8:
                    state.warned_80 = True
                    logging.warning(
                        f"[guardrail] Cost warning for run {run_id}: "
                        f"tokens={state.tokens_used}/{Config.GUARDRAIL_MAX_TOKENS_PER_RUN}, "
                        f"cost=${state.cost_used:.4f}/${Config.GUARDRAIL_MAX_COST_PER_RUN:.2f} "
                        f"(model={model})"
                    )

            # Hard-stop at 100%
            if state.tokens_used >= Config.GUARDRAIL_MAX_TOKENS_PER_RUN:
                return False, (
                    f"Token budget exceeded: {state.tokens_used:,} / "
                    f"{Config.GUARDRAIL_MAX_TOKENS_PER_RUN:,}"
                )
            if state.cost_used >= Config.GUARDRAIL_MAX_COST_PER_RUN:
                return False, (
                    f"Cost budget exceeded: ${state.cost_used:.4f} / "
                    f"${Config.GUARDRAIL_MAX_COST_PER_RUN:.2f}"
                )

        return True, None

    def charge(self, run_id: str, tokens: int, cost: float) -> None:
        """Add a completed LLM call to the per-run accumulator."""
        if not run_id:
            return

        with self._lock:
            state = self._runs.setdefault(run_id, _CostRunState())
            state.tokens_used += tokens
            state.cost_used += cost

    def end_run(self, run_id: str) -> None:
        """Purge a run's accumulator to prevent memory leaks."""
        if not run_id:
            return
        with self._lock:
            self._runs.pop(run_id, None)


# ---------------------------------------------------------------------------
# Quality Tracker
# ---------------------------------------------------------------------------

@dataclass
class _QualityRunRecord:
    """Single-run quality snapshot."""
    timestamp: float
    run_id: str
    avg_score: float


class QualityTracker:
    """
    Sliding-window evaluation-score tracker.

    Detects model degradation by watching the last ``window_size`` runs.
    Also tracks per-PDF consecutive low scores.
    """

    def __init__(self, window_size: int = 5):
        self._global_window: deque[_QualityRunRecord] = deque(maxlen=window_size)
        self._pdf_scores: Dict[str, List[float]] = {}  # pdf_path -> [score1, score2, ...]
        self._lock = threading.Lock()

    def record(self, run_id: str, avg_score: float) -> None:
        """Record the average evaluation score for a completed run."""
        with self._lock:
            self._global_window.append(
                _QualityRunRecord(timestamp=time.time(), run_id=run_id, avg_score=avg_score)
            )

    def record_pdf_score(self, pdf_path: str, score: float) -> None:
        """Record a per-PDF score (used to detect repeated failures on the same file)."""
        with self._lock:
            self._pdf_scores.setdefault(pdf_path, []).append(score)

    def is_degraded(self, pdf_path: Optional[str] = None) -> bool:
        """
        Returns ``True`` if quality has degraded.

        Global degradation: last 3 runs in the window all scored below threshold.
        Per-PDF degradation: 2 consecutive attempts on the same PDF scored below threshold.
        """
        threshold = Config.GUARDRAIL_QUALITY_SCORE_THRESHOLD

        with self._lock:
            # Global check
            if len(self._global_window) >= 3:
                last_three = list(self._global_window)[-3:]
                if all(r.avg_score < threshold for r in last_three):
                    logging.warning(
                        f"[guardrail] Global quality degraded: last 3 runs all < {threshold}"
                    )
                    return True

            # Per-PDF check
            if pdf_path:
                scores = self._pdf_scores.get(pdf_path, [])
                if len(scores) >= 2:
                    if scores[-1] < threshold and scores[-2] < threshold:
                        logging.warning(
                            f"[guardrail] Per-PDF quality degraded for {pdf_path}: "
                            f"last 2 attempts < {threshold}"
                        )
                        return True

        return False

    def reset_pdf(self, pdf_path: str) -> None:
        """Clear per-PDF scores (e.g. after a successful retry)."""
        with self._lock:
            self._pdf_scores.pop(pdf_path, None)


# ---------------------------------------------------------------------------
# Input Validator
# ---------------------------------------------------------------------------

class InputValidator:
    """Static PDF health checks before any LLM calls are attempted."""

    MAX_FILE_SIZE_MB = 100
    MIN_TEXT_LENGTH = 100

    @classmethod
    def validate(cls, pdf_path: str) -> None:
        """
        Validate a PDF file.

        Raises:
            InputValidationError: if the PDF is corrupted, too large, empty, or unreadable.
        """
        p = Path(pdf_path)

        # Existence / size
        if not p.exists():
            raise InputValidationError(f"PDF not found: {pdf_path}")
        if not p.is_file():
            raise InputValidationError(f"Not a regular file: {pdf_path}")

        size_bytes = p.stat().st_size
        if size_bytes == 0:
            raise InputValidationError(f"PDF is empty (0 bytes): {pdf_path}")
        if size_bytes > cls.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise InputValidationError(
                f"PDF too large ({size_bytes / (1024*1024):.1f} MB > {cls.MAX_FILE_SIZE_MB} MB): {pdf_path}"
            )

        # PDF structure check
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                if total_pages == 0:
                    raise InputValidationError(f"PDF has 0 pages: {pdf_path}")

                total_text = 0
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    total_text += len(text)

                if total_text < cls.MIN_TEXT_LENGTH:
                    # No text layer — this is okay for VLM fallback, but warn
                    logging.warning(
                        f"[guardrail] PDF has minimal text ({total_text} chars). "
                        f"Will fall back to VLM image detection: {pdf_path}"
                    )

        except InputValidationError:
            raise
        except Exception as e:
            raise InputValidationError(f"PDF is corrupted or unreadable: {pdf_path} ({e})")


# ---------------------------------------------------------------------------
# Concurrency Limiter
# ---------------------------------------------------------------------------

class ConcurrencyLimiter:
    """
    Process-level semaphore that caps parallel workflow invocations.

    Does NOT limit internal ThreadPoolExecutor workers within a single workflow.
    """

    def __init__(self, max_concurrent: int):
        self._sem = threading.Semaphore(max_concurrent)

    def acquire(self, timeout: float = 30.0) -> bool:
        """Try to acquire a slot. Returns ``True`` on success."""
        acquired = self._sem.acquire(timeout=timeout)
        if not acquired:
            logging.warning(
                f"[guardrail] Concurrency limit reached. Request timed out after {timeout}s."
            )
        return acquired

    def release(self) -> None:
        """Release a slot."""
        try:
            self._sem.release()
        except ValueError:
            # Semaphore over-released (shouldn't happen with correct try/finally)
            pass


# ---------------------------------------------------------------------------
# Guardrail Registry (singleton)
# ---------------------------------------------------------------------------

class GuardrailRegistry:
    """
    Central orchestrator for all guardrails.

    Usage:
        gr = get_guardrails()
        gr.input.validate(pdf_path)
        gr.cost.check(run_id, model)
        gr.cost.charge(run_id, tokens, cost)
        gr.quality.record(run_id, avg_score)
        gr.concurrency.acquire()
        ...
        gr.concurrency.release()
        gr.cost.end_run(run_id)
    """

    def __init__(self):
        self.input = InputValidator()
        self.cost = CostGuardrail()
        self.quality = QualityTracker()
        self.concurrency = ConcurrencyLimiter(Config.GUARDRAIL_MAX_CONCURRENT_RUNS)

    def end_run(self, run_id: str) -> None:
        """Clean up all per-run state."""
        self.cost.end_run(run_id)


_guardrails: Optional[GuardrailRegistry] = None


def get_guardrails() -> GuardrailRegistry:
    """Get or create the global GuardrailRegistry instance."""
    global _guardrails
    if _guardrails is None:
        _guardrails = GuardrailRegistry()
    return _guardrails
