#!/usr/bin/env python3
"""
Observability Dashboard — Reports on extraction run metrics, tokens, and costs.

Usage:
    python scripts/observability_dashboard.py
    python scripts/observability_dashboard.py --days 7
    python scripts/observability_dashboard.py --run-id abc123
    python scripts/observability_dashboard.py --export output/dashboard_report.json
"""

import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any


def load_metrics(metrics_dir: Path) -> List[Dict[str, Any]]:
    """Load all metrics JSON files."""
    runs = []
    for mf in metrics_dir.glob("*.json"):
        try:
            with open(mf) as f:
                run = json.load(f)
                run["_file_mtime"] = mf.stat().st_mtime
                runs.append(run)
        except Exception as e:
            print(f"⚠️  Skipping corrupt metrics file {mf}: {e}")
    return runs


def filter_by_date(runs: List[Dict], days: int) -> List[Dict]:
    """Filter runs to the last N days."""
    if days <= 0:
        return runs
    cutoff = datetime.now().timestamp() - (days * 24 * 3600)
    return [r for r in runs if r.get("_file_mtime", 0) > cutoff]


def find_run_by_id(runs: List[Dict], run_id: str) -> Dict:
    """Find a specific run by ID."""
    for r in runs:
        if r.get("run_id") == run_id:
            return r
    return {}


def format_duration(seconds: float) -> str:
    """Format duration as mm:ss or hh:mm:ss."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def format_cost(cost_usd: float) -> str:
    """Format cost with appropriate precision."""
    if cost_usd >= 1.0:
        return f"${cost_usd:.2f}"
    elif cost_usd >= 0.01:
        return f"${cost_usd:.3f}"
    elif cost_usd > 0:
        return f"${cost_usd:.6f}"
    return "$0.00"


def format_tokens(n: int) -> str:
    """Format large token counts with commas."""
    return f"{n:,}"


def print_summary(runs: List[Dict], days: int):
    """Print aggregated summary statistics."""
    if not runs:
        print("\n📊 No runs found in the selected period.")
        return

    total = len(runs)
    successful = sum(1 for r in runs if r.get("success"))
    failed = total - successful

    total_duration = sum(r.get("total_duration_sec", 0) for r in runs)
    total_llm_calls = sum(r.get("llm_calls", 0) for r in runs)
    total_retries = sum(r.get("retry_count", 0) for r in runs)
    total_cat_retries = sum(r.get("cat_retry_count", 0) for r in runs)
    total_review = sum(r.get("review_queue_count", 0) for r in runs)

    # Tokens
    total_prompt = sum(r.get("total_prompt_tokens", 0) for r in runs)
    total_completion = sum(r.get("total_completion_tokens", 0) for r in runs)
    total_tokens = sum(r.get("total_tokens", 0) for r in runs)

    # Costs
    total_cost = sum(r.get("total_cost_usd", 0.0) for r in runs)

    # Per-model
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
            model_totals[model]["cost_usd"] += usage.get("cost_usd", 0.0)

    period_label = f"last {days} days" if days > 0 else "all time"
    print(f"\n{'='*60}")
    print(f"📊 OBSERVABILITY DASHBOARD — {period_label.upper()}")
    print(f"{'='*60}")

    print(f"\n📁 RUNS")
    print(f"   Total runs:        {total}")
    print(f"   Successful:        {successful} ({successful/total*100:.1f}%)")
    print(f"   Failed:            {failed} ({failed/total*100:.1f}%)")
    print(f"   Total duration:    {format_duration(total_duration)}")
    print(f"   Avg duration:      {format_duration(total_duration/total) if total else 0}")

    print(f"\n🔁 RETRIES")
    print(f"   Extraction retries:     {total_retries}")
    print(f"   Categorization retries: {total_cat_retries}")
    print(f"   Avg retries per run:    {total_retries/total:.2f}" if total else "   Avg retries per run:    0.00")
    print(f"   Items needing review:   {total_review}")

    print(f"\n🤖 LLM CALLS")
    print(f"   Total calls:       {total_llm_calls:,}")
    print(f"   Avg calls per run: {total_llm_calls/total:.1f}" if total else "   Avg calls per run: 0.0")

    print(f"\n🪙 TOKENS")
    print(f"   Prompt tokens:     {format_tokens(total_prompt)}")
    print(f"   Completion tokens: {format_tokens(total_completion)}")
    print(f"   Total tokens:      {format_tokens(total_tokens)}")
    print(f"   Avg per run:       {format_tokens(int(total_tokens/total))}" if total else "   Avg per run:       0")

    print(f"\n💰 COSTS")
    print(f"   Total cost:        {format_cost(total_cost)}")
    print(f"   Avg per run:       {format_cost(total_cost/total) if total else '$0.00'}")
    print(f"   Avg per call:      {format_cost(total_cost/total_llm_calls) if total_llm_calls else '$0.00'}")

    if model_totals:
        print(f"\n📈 PER-MODEL BREAKDOWN")
        print(f"   {'Model':<25} {'Calls':>6} {'Tokens':>12} {'Cost':>12}")
        print(f"   {'-'*60}")
        for model, usage in sorted(model_totals.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
            cost_str = format_cost(usage["cost_usd"])
            print(f"   {model:<25} {usage['calls']:>6,} {usage['total_tokens']:>12,} {cost_str:>12}")

    # Evaluation scores
    eval_scores: Dict[str, List[float]] = {}
    for r in runs:
        for st, score in r.get("evaluation_scores", {}).items():
            eval_scores.setdefault(st, []).append(score)

    if eval_scores:
        print(f"\n📋 EVALUATION SCORES (avg)")
        for st, scores in sorted(eval_scores.items()):
            avg = sum(scores) / len(scores)
            print(f"   {st:<25} {avg:>6.2f}  (n={len(scores)})")

    print(f"\n{'='*60}")


def print_run_detail(run: Dict):
    """Print detailed view of a single run."""
    if not run:
        print("\n❌ Run not found.")
        return

    print(f"\n{'='*60}")
    print(f"🔍 RUN DETAIL — {run.get('run_id', 'unknown')}")
    print(f"{'='*60}")

    print(f"\n📄 PDF:          {run.get('pdf_file', 'N/A')}")
    print(f"⏰ Timestamp:    {run.get('timestamp', 'N/A')}")
    print(f"✅ Success:      {run.get('success', False)}")
    print(f"⏱️  Duration:     {format_duration(run.get('total_duration_sec', 0))}")
    print(f"📊 Statements:     {', '.join(run.get('statement_types', []))}")

    if run.get("error_message"):
        print(f"\n❌ Error:        {run['error_message']}")

    print(f"\n🔁 Retries:      extraction={run.get('retry_count', 0)}, categorization={run.get('cat_retry_count', 0)}")
    print(f"👀 Review queue:   {run.get('review_queue_count', 0)} items")

    print(f"\n🤖 LLM Calls:    {run.get('llm_calls', 0)}")
    print(f"🪙 Tokens:        prompt={format_tokens(run.get('total_prompt_tokens', 0))}, completion={format_tokens(run.get('total_completion_tokens', 0))}, total={format_tokens(run.get('total_tokens', 0))}")
    print(f"💰 Cost:          {format_cost(run.get('total_cost_usd', 0.0))}")

    if run.get("node_timings"):
        print(f"\n⏱️  NODE TIMINGS")
        for node, timing in sorted(run["node_timings"].items(), key=lambda x: x[1], reverse=True):
            print(f"   {node:<20} {format_duration(timing)}")

    if run.get("model_usage"):
        print(f"\n📈 PER-MODEL USAGE")
        print(f"   {'Model':<25} {'Calls':>6} {'Prompt':>12} {'Completion':>12} {'Total':>12} {'Cost':>12}")
        print(f"   {'-'*80}")
        for model, usage in sorted(run["model_usage"].items(), key=lambda x: x[1]["cost_usd"], reverse=True):
            print(f"   {model:<25} {usage['calls']:>6,} {usage['prompt_tokens']:>12,} {usage['completion_tokens']:>12,} {usage['total_tokens']:>12,} {format_cost(usage['cost_usd']):>12}")

    if run.get("evaluation_scores"):
        print(f"\n📋 EVALUATION SCORES")
        for st, score in sorted(run["evaluation_scores"].items()):
            print(f"   {st:<25} {score:>6.2f}")

    print(f"\n{'='*60}")


def print_recent_runs(runs: List[Dict], limit: int = 10):
    """Print a table of recent runs."""
    if not runs:
        return

    # Sort by mtime descending
    sorted_runs = sorted(runs, key=lambda r: r.get("_file_mtime", 0), reverse=True)[:limit]

    print(f"\n📅 RECENT RUNS (last {limit})")
    print(f"   {'Run ID':<10} {'PDF':<25} {'Status':<8} {'Duration':<10} {'Calls':<6} {'Tokens':<12} {'Cost':<10}")
    print(f"   {'-'*90}")
    for r in sorted_runs:
        status = "✅" if r.get("success") else "❌"
        pdf = r.get("pdf_file", "N/A")[:24]
        duration = format_duration(r.get("total_duration_sec", 0))
        calls = r.get("llm_calls", 0)
        tokens = format_tokens(r.get("total_tokens", 0))
        cost = format_cost(r.get("total_cost_usd", 0.0))
        print(f"   {r.get('run_id', '?'):<10} {pdf:<25} {status:<8} {duration:<10} {calls:<6,} {tokens:<12} {cost:<10}")


def export_report(runs: List[Dict], output_path: Path, days: int):
    """Export full report to JSON."""
    # Build comprehensive report
    report = {
        "generated_at": datetime.now().isoformat(),
        "period_days": days,
        "summary": {},
        "runs": [],
    }

    if runs:
        total = len(runs)
        successful = sum(1 for r in runs if r.get("success"))
        total_duration = sum(r.get("total_duration_sec", 0) for r in runs)
        total_llm_calls = sum(r.get("llm_calls", 0) for r in runs)
        total_prompt = sum(r.get("total_prompt_tokens", 0) for r in runs)
        total_completion = sum(r.get("total_completion_tokens", 0) for r in runs)
        total_tokens = sum(r.get("total_tokens", 0) for r in runs)
        total_cost = sum(r.get("total_cost_usd", 0.0) for r in runs)

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
                model_totals[model]["cost_usd"] += usage.get("cost_usd", 0.0)

        report["summary"] = {
            "total_runs": total,
            "successful": successful,
            "failed": total - successful,
            "success_rate_pct": round(successful / total * 100, 1) if total else 0,
            "total_duration_sec": round(total_duration, 2),
            "avg_duration_sec": round(total_duration / total, 2) if total else 0,
            "total_llm_calls": total_llm_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_run_usd": round(total_cost / total, 6) if total else 0,
            "per_model_usage": model_totals,
        }

        # Include individual run summaries
        for r in sorted(runs, key=lambda x: x.get("_file_mtime", 0), reverse=True):
            report["runs"].append({
                "run_id": r.get("run_id"),
                "pdf_file": r.get("pdf_file"),
                "timestamp": r.get("timestamp"),
                "success": r.get("success"),
                "duration_sec": r.get("total_duration_sec"),
                "llm_calls": r.get("llm_calls"),
                "prompt_tokens": r.get("total_prompt_tokens"),
                "completion_tokens": r.get("total_completion_tokens"),
                "total_tokens": r.get("total_tokens"),
                "cost_usd": r.get("total_cost_usd"),
                "retry_count": r.get("retry_count"),
                "cat_retry_count": r.get("cat_retry_count"),
                "review_queue_count": r.get("review_queue_count"),
                "error_message": r.get("error_message"),
            })

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n📤 Report exported to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Financial Data Extractor Observability Dashboard")
    parser.add_argument("--days", type=int, default=7, help="Number of days to include (0 = all)")
    parser.add_argument("--run-id", type=str, help="Show detail for a specific run")
    parser.add_argument("--export", type=str, help="Export report to JSON file")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    metrics_dir = base_dir / "output" / "metrics"

    if not metrics_dir.exists():
        print(f"\n📂 Metrics directory not found: {metrics_dir}")
        print("   No runs have been recorded yet.")
        return

    runs = load_metrics(metrics_dir)
    filtered = filter_by_date(runs, args.days)

    if args.run_id:
        run = find_run_by_id(runs, args.run_id)
        print_run_detail(run)
        return

    print_summary(filtered, args.days)
    print_recent_runs(filtered, limit=10)

    if args.export:
        export_report(filtered, Path(args.export), args.days)


if __name__ == "__main__":
    main()
