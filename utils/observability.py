"""
Observability module for the financial statement extraction system.

Provides:
- Run-level metrics (timing, success/failure, retries)
- Structured JSON logging
- LLM call tracking (duration, model, tokens)
- Node-level timing instrumentation
"""

import json
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

from config import Config


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


class Observability:
    """
    Central observability collector for extraction runs.

    Usage:
        obs = Observability()
        run_id = obs.start_run(pdf_path, statement_types)
        obs.log_node_timing("orchestrator", 2.5)
        obs.log_llm_call("qwen3.5", 1200, 100, 50)
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
                error_message: Optional[str] = None):
        """
        End an extraction run and save metrics.

        Args:
            run_id: The run ID from start_run()
            success: Whether the run completed successfully
            retry_count: Number of retry attempts
            error_message: Error message if failed
        """
        if run_id not in self._active_runs:
            return

        metrics = self._active_runs[run_id]
        metrics.success = success
        metrics.retry_count = retry_count
        metrics.error_message = error_message

        # Calculate total duration
        if run_id in self._start_times:
            metrics.total_duration_sec = round(time.time() - self._start_times[run_id], 2)
            del self._start_times[run_id]

        # Save metrics to JSON
        self._save_metrics(metrics)

        # Log run completion
        self.log_event(
            "run_completed",
            run_id=run_id,
            success=success,
            duration_sec=metrics.total_duration_sec,
            llm_calls=metrics.llm_calls,
            retry_count=retry_count
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
                     run_id: Optional[str] = None):
        """
        Log an LLM call with timing.

        Args:
            model: Model name used
            duration_ms: Call duration in milliseconds
            prompt: The prompt sent (optional, for debugging)
            response: The response received (optional, for debugging)
            run_id: Optional run ID to associate with
        """
        duration_sec = round(duration_ms / 1000, 3)

        if run_id and run_id in self._active_runs:
            metrics = self._active_runs[run_id]
            metrics.llm_calls += 1
            metrics.llm_total_duration_sec += duration_sec

        # Estimate tokens (rough: 4 chars per token)
        prompt_tokens = len(prompt) // 4 if prompt else 0
        response_tokens = len(response) // 4 if response else 0

        self.log_event(
            "llm_call",
            model=model,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            response_tokens=response_tokens,
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

    def get_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Get aggregated statistics for recent runs.

        Args:
            days: Number of days to include

        Returns:
            Dict with aggregated statistics
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

        return {
            "total_runs": total,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "avg_duration_sec": round(total_duration / total, 2) if total > 0 else 0,
            "total_llm_calls": total_llm_calls,
            "total_retries": total_retries,
            "avg_retries_per_run": round(total_retries / total, 2) if total > 0 else 0
        }


# Global instance for convenience
_observability: Optional[Observability] = None


def get_observability() -> Observability:
    """Get or create the global Observability instance."""
    global _observability
    if _observability is None:
        _observability = Observability()
    return _observability
