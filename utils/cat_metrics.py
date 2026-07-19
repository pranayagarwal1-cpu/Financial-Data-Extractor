"""
Categorization metrics — pure functions that compute detailed statistics
from categorized statement data.

Used by the categorizer node to build a rich metrics snapshot and by the
cat evaluator to validate quality.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


@dataclass
class CategorizationMetrics:
    """Snapshot of categorization quality for a single statement."""

    # Item counts
    total_items: int = 0
    section_headers: int = 0
    postable_items: int = 0
    categorized_items: int = 0
    uncategorized_items: int = 0
    needs_review_items: int = 0
    high_confidence_items: int = 0
    medium_confidence_items: int = 0
    low_confidence_items: int = 0
    unmatched_items: int = 0

    # Section-level counts
    revenue_items: int = 0
    revenue_categorized: int = 0
    cogs_items: int = 0
    cogs_categorized: int = 0
    opex_items: int = 0
    opex_categorized: int = 0
    other_items: int = 0
    other_categorized: int = 0

    # Rates (0.0–1.0)
    coverage_rate: float = 0.0
    high_conf_rate: float = 0.0
    medium_conf_rate: float = 0.0
    low_conf_rate: float = 0.0
    review_rate: float = 0.0
    unmatched_rate: float = 0.0

    # Section sanity (0.0–1.0)
    revenue_sanity: float = 0.0
    cogs_sanity: float = 0.0
    opex_sanity: float = 0.0
    other_sanity: float = 0.0
    overall_sanity: float = 0.0

    # Match-type distribution
    exact_matches: int = 0
    token_matches: int = 0
    llm_matches: int = 0
    llm_split_matches: int = 0
    llm_retry_matches: int = 0
    unmatched_matches: int = 0

    # Split items
    split_items: int = 0

    # Specific CoA-level sanity flags
    section_violations: List[dict] = field(default_factory=list)
    balance_sheet_codes_used: List[str] = field(default_factory=list)

    # LLM batch stats
    batch_count: int = 0
    batch_failure_count: int = 0
    llm_duration_ms: float = 0.0
    coa_context_tokens: int = 0

    # Memory
    memory_rules_applied: int = 0

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "total_items": self.total_items,
            "section_headers": self.section_headers,
            "postable_items": self.postable_items,
            "categorized_items": self.categorized_items,
            "uncategorized_items": self.uncategorized_items,
            "needs_review_items": self.needs_review_items,
            "high_confidence_items": self.high_confidence_items,
            "medium_confidence_items": self.medium_confidence_items,
            "low_confidence_items": self.low_confidence_items,
            "unmatched_items": self.unmatched_items,
            "coverage_rate": round(self.coverage_rate, 3),
            "high_conf_rate": round(self.high_conf_rate, 3),
            "medium_conf_rate": round(self.medium_conf_rate, 3),
            "low_conf_rate": round(self.low_conf_rate, 3),
            "review_rate": round(self.review_rate, 3),
            "unmatched_rate": round(self.unmatched_rate, 3),
            "revenue_items": self.revenue_items,
            "revenue_categorized": self.revenue_categorized,
            "revenue_sanity": round(self.revenue_sanity, 3),
            "cogs_items": self.cogs_items,
            "cogs_categorized": self.cogs_categorized,
            "cogs_sanity": round(self.cogs_sanity, 3),
            "opex_items": self.opex_items,
            "opex_categorized": self.opex_categorized,
            "opex_sanity": round(self.opex_sanity, 3),
            "other_items": self.other_items,
            "other_categorized": self.other_categorized,
            "other_sanity": round(self.other_sanity, 3),
            "overall_sanity": round(self.overall_sanity, 3),
            "exact_matches": self.exact_matches,
            "token_matches": self.token_matches,
            "llm_matches": self.llm_matches,
            "llm_split_matches": self.llm_split_matches,
            "llm_retry_matches": self.llm_retry_matches,
            "unmatched_matches": self.unmatched_matches,
            "split_items": self.split_items,
            "section_violations": self.section_violations,
            "balance_sheet_codes_used": self.balance_sheet_codes_used,
            "batch_count": self.batch_count,
            "batch_failure_count": self.batch_failure_count,
            "llm_duration_ms": round(self.llm_duration_ms, 2),
            "coa_context_tokens": self.coa_context_tokens,
            "memory_rules_applied": self.memory_rules_applied,
        }


# Section keywords for routing items to the right sanity bucket.
# Order matters: more specific patterns are checked first.
_SECTION_PATTERNS = [
    # COGS (checked before generic revenue/income)
    ("cogs", {"cost of goods", "cost of revenue", "cost of sales", "cogs"}),
    # Other income / other expense (checked before generic income/expense)
    ("other", {"other income", "other expense", "other revenue"}),
    # Interest / taxes / gains / losses (checked before generic expense)
    ("other", {"interest", "income tax", "corporate tax", "gain on", "loss on"}),
    # Revenue
    ("revenue", {"revenue", "income", "sales"}),
    # Operating expenses
    ("opex", {"operating expense", "g&a", "general and administrative", "selling expense"}),
    # Generic expense (catch-all after revenue and opex)
    ("opex", {"expense"}),
]


def _section_type(section_name: str) -> str:
    """Classify a section name into revenue, cogs, opex, other, or unknown."""
    name = section_name.lower()
    for sec_type, keywords in _SECTION_PATTERNS:
        if any(kw in name for kw in keywords):
            return sec_type
    return "unknown"


def _account_series(coa_code: Optional[str]) -> Optional[str]:
    """Return the account series (5xxx, 6xxx, 7xxx, 8xxx, 9xxx, 1xxx–4xxx)."""
    if not coa_code:
        return None
    s = str(coa_code).strip()
    if len(s) < 1 or not s[0].isdigit():
        return None
    first = s[0]
    if first in "1234":
        return "balance_sheet"
    if first == "5":
        return "5xxx"
    if first == "6":
        return "6xxx"
    if first == "7":
        return "7xxx"
    if first == "8":
        return "8xxx"
    if first == "9":
        return "9xxx"
    return None


def _is_valid_for_section(section_type: str, account_series: Optional[str]) -> bool:
    """Check whether an account series is valid for a given section type."""
    if account_series is None:
        return False
    mapping = {
        "revenue": {"5xxx"},
        "cogs": {"6xxx"},
        "opex": {"7xxx", "8xxx"},
        "other": {"8xxx", "9xxx"},
    }
    return account_series in mapping.get(section_type, set())


def compute_categorization_metrics(
    categorized_data: dict,
    batch_count: int = 0,
    batch_failure_count: int = 0,
    llm_duration_ms: float = 0.0,
    memory_rules_applied: int = 0,
    coa_context_tokens: int = 0,
) -> CategorizationMetrics:
    """
    Compute a full CategorizationMetrics snapshot from categorized statement data.

    Args:
        categorized_data: The categorized statement dict (with sections/rows).
        batch_count: Number of LLM batches sent.
        batch_failure_count: Number of LLM batches that failed.
        llm_duration_ms: Total wall-clock time spent in LLM calls.
        memory_rules_applied: Number of learned memory corrections applied.
        coa_context_tokens: Total tokens spent on CoA context across all batches —
            the full CoA dump by default, or a RAG-retrieved subset when
            Config.USE_RAG_COA_RETRIEVAL is on. Useful as a before/after
            comparison of prompt size.

    Returns:
        CategorizationMetrics dataclass with all computed fields.
    """
    metrics = CategorizationMetrics()
    metrics.batch_count = batch_count
    metrics.batch_failure_count = batch_failure_count
    metrics.llm_duration_ms = llm_duration_ms
    metrics.memory_rules_applied = memory_rules_applied
    metrics.coa_context_tokens = coa_context_tokens

    # (total_items, valid_items) per section type
    section_counts: Dict[str, Tuple[int, int]] = {
        "revenue": (0, 0),
        "cogs": (0, 0),
        "opex": (0, 0),
        "other": (0, 0),
        "unknown": (0, 0),
    }

    for section in categorized_data.get("sections", []):
        section_name = section.get("name", "")
        sec_type = _section_type(section_name)
        total_sec, cat_sec = section_counts[sec_type]

        for row in section.get("rows", []):
            if row.get("is_subtotal"):
                continue

            lt = row.get("line_type", "")
            if lt == "section_header":
                metrics.section_headers += 1
                continue

            metrics.postable_items += 1
            total_sec += 1

            cat = row.get("categorization", {})
            if not cat:
                metrics.uncategorized_items += 1
                continue

            coa_code = cat.get("coa_code")
            confidence = cat.get("confidence", "")
            match_type = cat.get("match_type", "")

            if coa_code:
                metrics.categorized_items += 1
            else:
                metrics.uncategorized_items += 1

            # Section sanity: count as valid only if categorized AND series matches section
            series = _account_series(coa_code)
            if coa_code and _is_valid_for_section(sec_type, series):
                cat_sec += 1

            if confidence == "high":
                metrics.high_confidence_items += 1
            elif confidence == "medium":
                metrics.medium_confidence_items += 1
            elif confidence == "low":
                metrics.low_confidence_items += 1
            elif confidence == "unmatched":
                metrics.unmatched_items += 1

            if cat.get("needs_review"):
                metrics.needs_review_items += 1

            if match_type == "exact":
                metrics.exact_matches += 1
            elif match_type == "token":
                metrics.token_matches += 1
            elif match_type == "llm":
                metrics.llm_matches += 1
            elif match_type == "llm_split":
                metrics.llm_split_matches += 1
            elif match_type == "llm_retry":
                metrics.llm_retry_matches += 1
            elif match_type == "unmatched":
                metrics.unmatched_matches += 1

            if cat.get("is_split"):
                metrics.split_items += 1

            # Section sanity checks
            series = _account_series(coa_code)
            if series == "balance_sheet":
                metrics.balance_sheet_codes_used.append(str(coa_code))

            if coa_code and not _is_valid_for_section(sec_type, series):
                metrics.section_violations.append({
                    "label": row.get("label", ""),
                    "section": section_name,
                    "assigned_code": coa_code,
                    "assigned_series": series,
                    "expected_for_section": sec_type,
                })

        section_counts[sec_type] = (total_sec, cat_sec)

    metrics.total_items = metrics.postable_items + metrics.section_headers
    metrics.revenue_items, metrics.revenue_categorized = section_counts["revenue"]
    metrics.cogs_items, metrics.cogs_categorized = section_counts["cogs"]
    metrics.opex_items, metrics.opex_categorized = section_counts["opex"]
    metrics.other_items, metrics.other_categorized = section_counts["other"]

    # Rates
    p = metrics.postable_items
    c = metrics.categorized_items
    metrics.coverage_rate = c / p if p > 0 else 0.0
    metrics.high_conf_rate = metrics.high_confidence_items / c if c > 0 else 0.0
    metrics.medium_conf_rate = metrics.medium_confidence_items / c if c > 0 else 0.0
    metrics.low_conf_rate = metrics.low_confidence_items / c if c > 0 else 0.0
    metrics.review_rate = metrics.needs_review_items / p if p > 0 else 0.0
    metrics.unmatched_rate = metrics.unmatched_items / p if p > 0 else 0.0

    # Section sanity rates
    def _rate(items: int, cat: int) -> float:
        return cat / items if items > 0 else 1.0

    metrics.revenue_sanity = _rate(metrics.revenue_items, metrics.revenue_categorized)
    metrics.cogs_sanity = _rate(metrics.cogs_items, metrics.cogs_categorized)
    metrics.opex_sanity = _rate(metrics.opex_items, metrics.opex_categorized)
    metrics.other_sanity = _rate(metrics.other_items, metrics.other_categorized)

    # Overall sanity weighted by item count
    total_weighted = (
        metrics.revenue_sanity * metrics.revenue_items +
        metrics.cogs_sanity * metrics.cogs_items +
        metrics.opex_sanity * metrics.opex_items +
        metrics.other_sanity * metrics.other_items
    )
    metrics.overall_sanity = total_weighted / p if p > 0 else 0.0

    return metrics


def compute_delta_metrics(
    previous: CategorizationMetrics,
    current: CategorizationMetrics,
) -> dict:
    """
    Compute the delta between two metric snapshots (e.g. before/after retry).

    Returns a dict with absolute and relative changes for key fields.
    """
    def _delta(prev: float, curr: float) -> dict:
        abs_delta = round(curr - prev, 3)
        rel_delta = round((curr - prev) / prev, 3) if prev != 0 else None
        return {"previous": prev, "current": curr, "absolute": abs_delta, "relative": rel_delta}

    return {
        "coverage_rate": _delta(previous.coverage_rate, current.coverage_rate),
        "review_rate": _delta(previous.review_rate, current.review_rate),
        "high_conf_rate": _delta(previous.high_conf_rate, current.high_conf_rate),
        "overall_sanity": _delta(previous.overall_sanity, current.overall_sanity),
        "categorized_items": _delta(previous.categorized_items, current.categorized_items),
        "needs_review_items": _delta(previous.needs_review_items, current.needs_review_items),
        "section_violations": _delta(len(previous.section_violations), len(current.section_violations)),
        "split_items": _delta(previous.split_items, current.split_items),
    }
