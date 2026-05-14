"""
Observability module for the financial statement extraction system.

Provides:
- Run-level metrics (timing, success/failure, retries, tokens, costs)
- Structured JSON logging
- LLM call tracking (duration, model, actual tokens, estimated cost)
- Node-level timing instrumentation
- Per-model token and cost breakdown
"""

import json
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

from config import Config


# Cost per 1K tokens (USD) — update as pricing changes
MODEL_COST_RATES: Dict[str, Dict[str, float]] = {
    # Anthropic pricing (as of May 2026)
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.25, "output": 1.25},
    # Local Ollama models — $0 (compute cost not tracked here)
    "qwen3.5": {"input": 0.0, "output": 0.0},
    "qwen2.5": {"input": 0.0, "output": 0.0},
    "llama3": {"input": 0.0, "output": 0.0},
    "llama3.1": {"input": 0.0, "output": 0.0},
    "mistral": {"input": 0.0, "output": 0.0},
    "mixtral": {"input": 0.0, "output": 0.0},
    "deepseek-r1": {"input": 0.0, "output": 0.0},
}


def _get_model_rate(model: str) -> Dict[str, float]:
    """Look up cost rate for a model name (handles tag suffixes like ':397b-cloud')."""
    base = model.split(":")[0].split("-")[0]  # e.g., "qwen3.5:397b-cloud" -> "qwen3.5"
    # Try exact match first
    if model in MODEL_COST_RATES:
        return MODEL_COST_RATES[model]
    # Try base name
    if base in MODEL_COST_RATES:
        return MODEL_COST_RATES[base]
    # Try prefix match for claude models
    if model.startswith("claude-"):
        for key in MODEL_COST_RATES:
            if model.startswith(key):
                return MODEL_COST_RATES[key]
    return {"input": 0.0, "output": 0.0}


def _calculate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """Calculate estimated cost in USD."""
    rate = _get_model_rate(model)
    input_cost = (prompt_tokens / 1000) * rate["input"]
    output_cost = (completion_tokens / 1000) * rate["output"]
    return round(input_cost + output_cost, 6)


@dataclass
class RunMetrics:
    """Metrics for a single extraction run."""
    run_id: str
    timestamp: str
    pdf_file: str
    statement_types: List[str]
    total_duration_sec: float = 0.0
    node_timings: Dict[str, float] = field(default_factory=dict)
    llm_calls: int = 0
    llm_total_duration_sec: float = 0.0
    retry_count: int = 0
    success: bool = False
    evaluation_scores: Dict[str, float] = field(default_factory=dict)
    error_message: Optional[str] = None

    # Token tracking
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0

    # Cost tracking
    total_cost_usd: float = 0.0

    # Per-model breakdown
    model_usage: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Categorization-specific
    cat_retry_count: int = 0
    review_queue_count: int = 0
    cat_metrics: Dict[str, Any] = field(default_factory=dict)
    cat_evaluation_scores: Dict[str, float] = field(default_factory=dict)


class Observability:
    """
    Central observability collector for extraction runs.

    Usage:
        obs = Observability()
        run_id = obs.start_run(pdf_path, statement_types)
        obs.log_node_timing("orchestrator", 2.5)
        obs.log_llm_call("claude-sonnet-4-6", 1200, prompt_tokens=100, completion_tokens=50)
        obs.end_run(run_id, success=True, retry_count=1)
    """

    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.metrics_dir = self.base_dir / "output" / "metrics"
        self.logs_dir = self.base_dir / "output" / "logs"
        self._ensure_dirs()

        # In-memory state for active runs
        self._active_runs: Dict[str, RunMetrics] = {}
        self._start_times: Dict[str, float] = {}

    def _ensure_dirs(self):
        """Create output directories if they don't exist."""
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        Config.METRICS_DIR = str(self.metrics_dir)
        Config.LOGS_DIR = str(self.logs_dir)

    def start_run(self, pdf_path: str, statement_types: list) -> str:
        """
        Start a new extraction run.

        Args:
            pdf_path: Path to the PDF being processed
            statement_types: List of StatementType enums to extract

        Returns:
            run_id: Unique identifier for this run
        """
        run_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()
        pdf_name = Path(pdf_path).name

        # Convert statement types to strings
        stmt_names = [st.value if hasattr(st, 'value') else str(st) for st in statement_types]

        self._active_runs[run_id] = RunMetrics(
            run_id=run_id,
            timestamp=timestamp,
            pdf_file=pdf_name,
            statement_types=stmt_names
        )
        self._start_times[run_id] = time.time()

        self.log_event("run_started", run_id=run_id, pdf_file=pdf_name, statement_types=stmt_names)
        return run_id

    def end_run(self, run_id: str, success: bool, retry_count: int = 0,
                cat_retry_count: int = 0, review_queue_count: int = 0,
                error_message: Optional[str] = None):
        """
        End an extraction run and save metrics.

        Args:
            run_id: The run ID from start_run()
            success: Whether the run completed successfully
            retry_count: Number of extraction retry attempts
            cat_retry_count: Number of categorization retry attempts
            review_queue_count: Number of items flagged for human review
            error_message: Error message if failed
        """
        if run_id not in self._active_runs:
            return

        metrics = self._active_runs[run_id]
        metrics.success = success
        metrics.retry_count = retry_count
        metrics.cat_retry_count = cat_retry_count
        metrics.review_queue_count = review_queue_count
        metrics.error_message = error_message

        # Calculate total duration
        if run_id in self._start_times:
            metrics.total_duration_sec = round(time.time() - self._start_times[run_id], 2)
            del self._start_times[run_id]

        # Calculate derived totals
        metrics.total_tokens = metrics.total_prompt_tokens + metrics.total_completion_tokens

        # Save metrics to JSON
        self._save_metrics(metrics)

        # Log run completion
        self.log_event(
            "run_completed",
            run_id=run_id,
            success=success,
            duration_sec=metrics.total_duration_sec,
            llm_calls=metrics.llm_calls,
            retry_count=retry_count,
            cat_retry_count=cat_retry_count,
            review_queue_count=review_queue_count,
            total_tokens=metrics.total_tokens,
            total_cost_usd=metrics.total_cost_usd,
        )

        # Clean up
        del self._active_runs[run_id]

    def log_node_timing(self, node_name: str, duration_ms: float, run_id: Optional[str] = None):
        """
        Log timing for a workflow node.

        Args:
            node_name: Name of the node (orchestrator, extractor, evaluator, save_outputs)
            duration_ms: Duration in milliseconds
            run_id: Optional run ID to associate with
        """
        duration_sec = round(duration_ms / 1000, 3)

        if run_id and run_id in self._active_runs:
            self._active_runs[run_id].node_timings[node_name] = duration_sec

        self.log_event(
            "node_timing",
            node=node_name,
            duration_ms=duration_ms,
            run_id=run_id
        )

    def log_llm_call(self, model: str, duration_ms: float,
                     prompt: Optional[str] = None,
                     response: Optional[str] = None,
                     prompt_tokens: Optional[int] = None,
                     completion_tokens: Optional[int] = None,
                     run_id: Optional[str] = None):
        """
        Log an LLM call with timing and token usage.

        Args:
            model: Model name used
            duration_ms: Call duration in milliseconds
            prompt: The prompt sent (optional, for debugging)
            response: The response received (optional, for debugging)
            prompt_tokens: Actual or estimated prompt token count
            completion_tokens: Actual or estimated completion token count
            run_id: Optional run ID to associate with
        """
        duration_sec = round(duration_ms / 1000, 3)

        # Use actual counts if provided, otherwise estimate from text length
        if prompt_tokens is None and prompt:
            from utils.token_counter import count_tokens
            prompt_tokens = count_tokens(prompt, model)
        if completion_tokens is None and response:
            from utils.token_counter import count_tokens
            completion_tokens = count_tokens(response, model)

        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        total_tokens = prompt_tokens + completion_tokens
        cost = _calculate_cost(prompt_tokens, completion_tokens, model)

        if run_id and run_id in self._active_runs:
            metrics = self._active_runs[run_id]
            metrics.llm_calls += 1
            metrics.llm_total_duration_sec += duration_sec
            metrics.total_prompt_tokens += prompt_tokens
            metrics.total_completion_tokens += completion_tokens
            metrics.total_cost_usd = round(metrics.total_cost_usd + cost, 6)

            # Per-model breakdown
            if model not in metrics.model_usage:
                metrics.model_usage[model] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
            metrics.model_usage[model]["calls"] += 1
            metrics.model_usage[model]["prompt_tokens"] += prompt_tokens
            metrics.model_usage[model]["completion_tokens"] += completion_tokens
            metrics.model_usage[model]["total_tokens"] += total_tokens
            metrics.model_usage[model]["cost_usd"] = round(
                metrics.model_usage[model]["cost_usd"] + cost, 6
            )

        self.log_event(
            "llm_call",
            model=model,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            run_id=run_id
        )

    def log_evaluation_score(self, statement_type: str, score: float,
                             details: Optional[Dict] = None,
                             run_id: Optional[str] = None):
        """
        Log an evaluation score for a statement.

        Args:
            statement_type: Type of statement evaluated
            score: Overall score (0-10)
            details: Breakdown of scores by criterion
            run_id: Optional run ID to associate with
        """
        if run_id and run_id in self._active_runs:
            self._active_runs[run_id].evaluation_scores[statement_type] = score

        self.log_event(
            "evaluation",
            statement_type=statement_type,
            score=score,
            details=details,
            run_id=run_id
        )

    def log_check_outcomes(self, statement_type: str, findings: list[dict],
                           run_id: Optional[str] = None):
        """
        Log individual programmatic check outcomes (PASS / FAIL / ADVISORY).

        Args:
            statement_type: Type of statement evaluated
            findings: List of finding dicts with check_id, status, message
            run_id: Optional run ID to associate with
        """
        self.log_event(
            "check_outcomes",
            statement_type=statement_type,
            findings=findings,
            run_id=run_id,
        )

    def log_event(self, event_type: str, **kwargs):
        """
        Log a structured event to the JSON Lines log file.

        Args:
            event_type: Type of event (run_started, node_timing, llm_call, etc.)
            **kwargs: Additional event data
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            **kwargs
        }

        # Ensure directories exist (in case they were cleaned up)
        self._ensure_dirs()

        # Write to daily log file
        log_file = self.logs_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def _save_metrics(self, metrics: RunMetrics):
        """Save run metrics to a JSON file."""
        metrics_file = self.metrics_dir / f"{metrics.run_id}.json"
        with open(metrics_file, "w") as f:
            json.dump(asdict(metrics), f, indent=2)

    def get_recent_runs(self, limit: int = 10) -> List[Dict]:
        """
        Get recent run metrics from disk.

        Args:
            limit: Maximum number of runs to return

        Returns:
            List of run metrics dicts, sorted by timestamp descending
        """
        metrics_files = sorted(self.metrics_dir.glob("*.json"),
                               key=lambda f: f.stat().st_mtime, reverse=True)

        runs = []
        for mf in metrics_files[:limit]:
            with open(mf) as f:
                runs.append(json.load(f))
        return runs

    def log_categorization_metrics(self, metrics: dict, run_id: Optional[str] = None):
        """
        Log detailed categorization metrics.

        Args:
            metrics: Dict from CategorizationMetrics.to_dict()
            run_id: Optional run ID to associate with
        """
        if run_id and run_id in self._active_runs:
            self._active_runs[run_id].cat_metrics = metrics

        self.log_event(
            "categorization_metrics",
            metrics=metrics,
            run_id=run_id
        )

    def log_cat_evaluation_score(self, statement_type: str, score: float,
                                 details: Optional[Dict] = None,
                                 run_id: Optional[str] = None):
        """
        Log a categorization evaluation score.

        Args:
            statement_type: Type of statement evaluated
            score: Overall score (0-10)
            details: Breakdown of scores by criterion
            run_id: Optional run ID to associate with
        """
        if run_id and run_id in self._active_runs:
            self._active_runs[run_id].cat_evaluation_scores[statement_type] = score

        self.log_event(
            "cat_evaluation",
            statement_type=statement_type,
            score=score,
            details=details,
            run_id=run_id
        )

    def get_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Get aggregated statistics for recent runs.

        Args:
            days: Number of days to include

        Returns:
            Dict with aggregated statistics including tokens and costs
        """
        cutoff = datetime.now().timestamp() - (days * 24 * 3600)

        runs = []
        for mf in self.metrics_dir.glob("*.json"):
            if mf.stat().st_mtime > cutoff:
                with open(mf) as f:
                    runs.append(json.load(f))

        if not runs:
            return {"total_runs": 0}

        # Calculate stats
        total = len(runs)
        successful = sum(1 for r in runs if r["success"])
        total_llm_calls = sum(r.get("llm_calls", 0) for r in runs)
        total_duration = sum(r.get("total_duration_sec", 0) for r in runs)
        total_retries = sum(r.get("retry_count", 0) for r in runs)
        total_cat_retries = sum(r.get("cat_retry_count", 0) for r in runs)
        total_review_queue = sum(r.get("review_queue_count", 0) for r in runs)

        # Token stats
        total_prompt_tokens = sum(r.get("total_prompt_tokens", 0) for r in runs)
        total_completion_tokens = sum(r.get("total_completion_tokens", 0) for r in runs)
        total_tokens = sum(r.get("total_tokens", 0) for r in runs)

        # Cost stats
        total_cost = sum(r.get("total_cost_usd", 0.0) for r in runs)

        # Per-model aggregation
        model_totals: Dict[str, Dict[str, Any]] = {}
        for r in runs:
            for model, usage in r.get("model_usage", {}).items():
                if model not in model_totals:
                    model_totals[model] = {
                        "calls": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                    }
                model_totals[model]["calls"] += usage.get("calls", 0)
                model_totals[model]["prompt_tokens"] += usage.get("prompt_tokens", 0)
                model_totals[model]["completion_tokens"] += usage.get("completion_tokens", 0)
                model_totals[model]["total_tokens"] += usage.get("total_tokens", 0)
                model_totals[model]["cost_usd"] = round(
                    model_totals[model]["cost_usd"] + usage.get("cost_usd", 0.0), 6
                )

        # Categorization stats
        cat_metrics_list = [r.get("cat_metrics", {}) for r in runs if r.get("cat_metrics")]
        total_cat_items = sum(m.get("postable_items", 0) for m in cat_metrics_list)
        total_cat_categorized = sum(m.get("categorized_items", 0) for m in cat_metrics_list)
        total_cat_review = sum(m.get("needs_review_items", 0) for m in cat_metrics_list)
        total_cat_split = sum(m.get("split_items", 0) for m in cat_metrics_list)
        total_cat_violations = sum(len(m.get("section_violations", [])) for m in cat_metrics_list)
        avg_coverage = (
            sum(m.get("coverage_rate", 0) for m in cat_metrics_list) / len(cat_metrics_list)
            if cat_metrics_list else 0
        )
        avg_review_rate = (
            sum(m.get("review_rate", 0) for m in cat_metrics_list) / len(cat_metrics_list)
            if cat_metrics_list else 0
        )

        # Cat evaluation stats
        cat_eval_scores = []
        for r in runs:
            for stmt_type, score in r.get("cat_evaluation_scores", {}).items():
                cat_eval_scores.append(score)
        avg_cat_eval_score = round(sum(cat_eval_scores) / len(cat_eval_scores), 2) if cat_eval_scores else 0

        return {
            "total_runs": total,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "avg_duration_sec": round(total_duration / total, 2) if total > 0 else 0,
            "total_llm_calls": total_llm_calls,
            "total_retries": total_retries,
            "total_cat_retries": total_cat_retries,
            "avg_retries_per_run": round(total_retries / total, 2) if total > 0 else 0,
            "total_review_queue": total_review_queue,
            "avg_review_queue_per_run": round(total_review_queue / total, 2) if total > 0 else 0,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "avg_cost_per_run_usd": round(total_cost / total, 4) if total > 0 else 0,
            "per_model_usage": model_totals,
            # Categorization
            "cat_runs_with_metrics": len(cat_metrics_list),
            "cat_total_items": total_cat_items,
            "cat_total_categorized": total_cat_categorized,
            "cat_total_review": total_cat_review,
            "cat_total_split": total_cat_split,
            "cat_total_violations": total_cat_violations,
            "cat_avg_coverage_rate": round(avg_coverage, 3),
            "cat_avg_review_rate": round(avg_review_rate, 3),
            "cat_avg_eval_score": avg_cat_eval_score,
        }


# Global instance for convenience
_observability: Optional[Observability] = None


def get_observability() -> Observability:
    """Get or create the global Observability instance."""
    global _observability
    if _observability is None:
        _observability = Observability()
    return _observability
