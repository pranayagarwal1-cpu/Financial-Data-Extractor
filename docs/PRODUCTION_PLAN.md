# Financial Statement Extractor — Production Plan & Evaluation Rubric

**Implementation status: ALL 6 PHASES COMPLETE** (as of 2026-05-10)

---

## System Goal

Automatically extract structured financial statement data (Balance Sheet, Income Statement, Cash Flow Statement) from PDFs with high accuracy, minimal human review, and full auditability. Reduce manual data entry time from 30+ minutes per report to under 1 minute.

The system must:
- Handle scanned PDFs, inverted images, rotated pages, and multi-page tables
- Validate extraction quality programmatically before presenting output
- Provide human-readable quality reports alongside every deliverable
- Support A/B testing (guardrails on/off) for continuous improvement

---

## High-Level Architecture

```
┌──────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PDF Input  │────▶│ Orchestrator│────▶│  Extractor  │────▶│  Evaluator  │
└──────────────┘     │  (detect)   │     │  (VLM + OCR)│     │  (judge)    │
                     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                    │
                              ┌──────────┬──────────────────────────┘
                              │ retry?   │
                              ▼          ▼
                        ┌─────────┐  ┌──────────┐  ┌─────────────┐
                        │Extractor│  │Categorizer│  │ Save Outputs│
                        │(retry)  │  │(CoA map) │  │ JSON + Excel│
                        └─────────┘  └──────────┘  └──────┬──────┘
                                                            │
                                                            ▼
                                                   ┌────────────────┐
                                                   │ Quality Report │
                                                   │  (Excel tab)   │
                                                   └────────────────┘
```

**Key components:**
- **Orchestrator** — Detects statement pages, validates input, coordinates retries
- **Extractor** — Rasterizes PDF pages, runs VLM for structured JSON extraction, OCR fallback for scanned PDFs
- **Evaluator** — 4-section quality rubric (Coverage, Format, Structure, Content) with 43 programmatic checks + LLM-as-Judge
- **Categorizer** — Maps extracted line items to Chart of Accounts (CoA) codes
- **Save Outputs** — Generates JSON and Excel; Excel includes a "Quality Report" tab

---

## 4-Section Evaluation Rubric

### Section A: Coverage (7 checks)

| # | Check | Detection Method | Pass Threshold |
|---|-------|-----------------|----------------|
| A1 | Required sections present | Keyword matching on section names | All required found |
| A2 | Missing value ratio | Count empty/null / total values | < 20% |
| A3 | Page coverage | Detected pages vs expected span (from VLM) | All pages found |
| A4 | Row count sanity | Count rows per section; flag extreme outliers vs benchmarks | Within ±50% of typical |
| A5 | Fields present but not extracted (missed line items) | Ground-truth: OCR text has numbers not present in any row value | < 3 missed values |
| A6 | Hallucinated fields (invented rows) | Ground-truth: row label not in OCR text; OR row value not in OCR text | < 2 invented rows |
| A7 | Comparative period integrity | Jaccard similarity of normalized labels across periods | ≥ 90% overlap |

### Section B: Format (7 checks)

| # | Check | Detection Method | Pass Threshold |
|---|-------|-----------------|----------------|
| B1 | Numeric parseability | `_parse_amount()` on every value; count failures | ≥ 95% parsable |
| B2 | JSON structure valid | Pydantic `StatementData.model_validate()` | No validation errors |
| B3 | Period consistency | Same `periods` array across all sections | Identical |
| B4 | Column alignment | `len(values) == len(periods)` for every row | 100% |
| B5 | Period labels are dates, not ratios | Regex: reject `%, change, ratio` in `periods` | None found |
| B6 | Currency format consistency | Regex per section: all values use same currency symbol | Consistent within section |
| B7 | Date format normalization | `periods` array parsed by `dateutil`; flag unparseable | All parseable |

### Section C: Structure (14 checks)

| # | Check | Detection Method | Pass Threshold |
|---|-------|-----------------|----------------|
| C1 | Balance sheet equation | Assets = Liabilities + Equity (tiered tolerance) | Matches |
| C2 | Income statement equations | Gross Profit = Revenue − COGS; Operating Income = Gross − OpEx; Net Income = Revenue − Expenses (tiered tolerance) | Matches |
| C3 | Cash flow equation | Net Change = Ending Cash − Beginning Cash (tiered tolerance) | Matches |
| C4 | Section sum reconciliation | Subtotals = sum of leaf children (tiered tolerance) | Matches |
| C5 | Indent normalization | `_normalize_indent_levels()` corrects flat subtotals | No flat subtotals remain |
| C6 | Hierarchy validation | Every subtotal at indent ≥0 has ≥1 child with higher indent | 100% |
| C7 | Page orientation events | Log rotation/inversion from `_auto_correct_orientation()` | Confidence ≥ 2.5 if rotated |
| C8 | Table spanning pages | Count deduplicated rows on multi-page merge; flag gaps | < 10% dedup rate |
| C9 | Multicolumn layout safety | Verify no percentage columns leaked into `periods` or `values` | None found |
| C10 | Header/footer bleed | Denylist: `"Page"`, `"Confidential"`, file name in extracted labels | None found |
| C11 | Section boundary coherence | No Revenue keywords in Expense section, etc. | No mixing |
| C12 | Duplicate row detection | Exact `(label, values)` duplicate within section | None found |
| C13 | Row order integrity | Subtotal index > last child index within section | 100% |
| C14 | Cross-statement reconciliation (if both IS + BS present) | Net Income ≈ Δ Retained Earnings (±3% tolerance, ±5% advisory) | Matches |

### Section D: Content (15 checks)

| # | Check | Detection Method | Pass Threshold |
|---|-------|-----------------|----------------|
| D1 | Misread numbers / digit transposition | Hallucination ground-truth: value string not in OCR text | < 3 missing |
| D2 | Hallucination / ground-truth (subtotals) | Tier 1: all subtotal values must appear in OCR | < 3 missing |
| D3 | Hallucination / ground-truth (all values) | Tier 2: all values must appear in OCR | < 5 missing |
| D4 | Label fuzzy accuracy | Normalized extracted label not in normalized OCR text | < 3 missing |
| D5 | Value range sanity | COGS > Revenue, or value > 10× section median | None |
| D6 | Sign consistency | Source has `(1,234)` but extracted `1,234`, or vice versa | < 3 mismatches |
| D7 | Truncated fields | Label ends with `..`, `...`, `…` | ADVISORY only (never FAIL) |
| D8 | Merged cells | `len(values) < len(periods)` on non-subtotal row → possible merge split | None |
| D9 | Unit scale errors | All values in section end in `.00` except one ending in `0` | None |
| D10 | OCR artifacts in labels | Non-ASCII printable chars (e.g., `€`, `Ø`, `©`) in labels | None |
| D11 | Decimal precision drift | Within a column, flag values with different implied precision than peers | < 3 outliers |
| D12 | VLM interpolation (invented subtotals) | Subtotal value not in OCR AND no arithmetic path from children | Flag |
| D13 | Duplicate values across periods | Within one row, `len(set(values)) == 1` and `len(values) > 1` | Materiality filter: ADVISORY if <5% of section total, FAIL otherwise |
| D14 | Materiality scoring | Discrepancy % relative to subtotal value and total revenue/assets | < 2% of subtotal, < 1% of total |
| D15 | Row order integrity | Children appear before subtotal in source order | 100% |

---

## Scoring & Pass Criteria

### Section Weights

| Section | Weight | Rationale |
|---|---|---|
| Coverage (A) | 20% | Missing data is recoverable via retry |
| Format (B) | 20% | Format issues block downstream ingestion |
| Structure (C) | 30% | Equation failures are high-stakes |
| Content (D) | 30% | Hallucination risk is highest-stakes |

### LLM-as-Judge

The judge does **not** override programmatic scores. It provides a qualitative confidence adjustment (±1.0 max per section) and a mandatory written summary.

```
final_score(section) = clamp(programmatic_score + judge_adjustment, 0, 10)
judge_adjustment ∈ [-1.0, +1.0]
overall = weighted_average(A, B, C, D)
```

### Pass Criteria

```python
overall >= 6.0 and not any_hard_fail
```

Hard-fail checks (cannot PASS if any of these fail):
- A1 (missing required sections)
- B2 (JSON invalid)
- C1–C3 (equation failures)

### Retry Triggers

```python
RETRY_POLICY = {
    "max_attempts": 3,
    "retry_if": {
        "min_overall_score": 6.0,
        "hard_fail_checks": ["B2", "A1", "C1", "C2", "C3"],
        "never_retry_checks": ["C7", "A3"]  # structural — retry won't fix
    }
}
```

---

## Gap Fixes

### Gap 1: Retry Strategy ✅ Implemented

**Trigger Condition — Score-Based with Check Overrides**

| Failure Category | Prompt Injection |
|---|---|
| A5/A6 — missed/hallucinated rows | "The previous extraction missed rows or invented rows not in the document. Re-examine the pages carefully. Do not invent rows not visible in the document." |
| B4 — column misalignment | "Every row must have exactly the same number of values as there are period columns. Do not merge or skip columns." |
| C1–C3 — equation failure | "Your prior extraction failed accounting equation checks. Re-examine totals carefully and ensure arithmetic relationships hold." |
| D6 — sign errors | "Check parenthetical numbers — `(1,234)` means negative. Do not drop the sign." |

**Retry Escalation:**
- Attempt 1 — VLM only
- Attempt 2 — VLM + OCR diff as additional context
- Attempt 3 — Switch to `Config.RETRY_EXTRACTION_MODEL`

**Implementation:** `config.py`, `agents/orchestrator.py`, `agents/extractor.py`

---

### Gap 2: Score Deduplication (Penalty Ledger) ✅ Implemented

`PenaltyLedger` prevents double-penalizing the same underlying error across Coverage (A) and Content (D). Both sections still *report* the finding in the Quality Report, but only one *penalizes*.

```python
@dataclass
class PenaltyLedger:
    penalized: set[str] = field(default_factory=set)
    def charge(self, check_id: str, item_key: str) -> bool:
        key = f"{check_id}:{item_key}"
        if key in self.penalized:
            return False
        self.penalized.add(key)
        return True
```

**Implementation:** `agents/evaluator.py`

---

### Gap 3: LLM Judge Specification ✅ Implemented

The judge provides a qualitative confidence adjustment (±1.0 max per section) and a mandatory written summary. It acts as a "sanity layer" that catches things rules miss.

**Judge Prompt Schema:** Strict JSON output contract with `coverage_adjustment`, `format_adjustment`, `structure_adjustment`, `content_adjustment`, `overall_confidence`, `summary`, and `flags`.

**Failure Handling:** If the judge response fails to parse, fall back to `adjustment=0` for all sections and log `judge_parse_error`. Never block the pipeline on judge failure.

**Implementation:** `agents/evaluator.py` (`JUDGE_PROMPT`)

---

### Gap 4: Equation Tolerance — Hybrid Absolute/Relative ✅ Implemented

Tiered tolerance scaled to magnitude:

```python
def equation_tolerance(value: Decimal) -> Decimal:
    abs_value = abs(value)
    if abs_value < 10_000:
        return Decimal("5.0")                       # flat $5 for small values
    elif abs_value < 1_000_000:
        return abs_value * Decimal("0.02")          # 2% for mid-range
    elif abs_value < 100_000_000:
        return abs_value * Decimal("0.01")          # 1% for large
    else:
        return min(abs_value * Decimal("0.005"), Decimal("500_000"))  # 0.5%, capped at $500K
```

**Materiality gate:** discrepancy under $1K absolute auto-passes regardless of percentage.

**C14 Special Case:** Cross-statement reconciliation uses ±3% tolerance; ADVISORY within ±5%.

**Implementation:** `agents/evaluator.py` (`_equation_tolerance()`, `_materiality_gate()`)

---

### Gap 5: Automated Verification Plan ✅ Implemented

```
tests/
├── fixtures/
│   ├── known_good/               # PDFs that should PASS with scores > 8.0
│   ├── known_bad/
│   │   ├── missing_sections.pdf  → expect A1 FAIL
│   │   ├── bad_rotation.pdf      → expect C7 ADVISORY
│   │   ├── hallucinated_rows.pdf → expect A6 FAIL, D3 FAIL
│   │   └── equation_mismatch.pdf → expect C1 FAIL
│   └── edge_cases/
│       ├── flat_lease_payments.pdf   → D13 should be ADVISORY not FAIL
│       └── unaudited_periods.pdf     → B5 should not false-positive
├── test_evaluator.py
├── test_excel_report.py
└── test_pipeline.py
```

**Implementation:** `tests/` (new directory and files)

---

## Performance Impact

| Component | Current | With New Checks |
|-----------|---------|----------------|
| Extraction (VLM) | ~10-30s | **Unchanged** |
| Evaluator programmatic | ~100ms | ~300ms |
| Ground-truth (values + labels) | ~200ms | ~500ms |
| LLM judge | ~1-2s | ~1.2-2.4s (prompt slightly larger) |
| **Total pipeline** | ~12-35s | **~12.3-35.7s** (+1-3%) |

Optional toggle: `Config.ENABLE_DEEP_CONTENT_CHECKS` (default True) can disable label ground-truth and materiality scoring if latency is critical.

---

## Files Affected

| Fix | File(s) to Update |
|---|---|
| Retry strategy | `agents/orchestrator.py`, `agents/extractor.py`, `config.py` |
| Penalty ledger | `agents/evaluator.py` |
| LLM judge spec | `agents/evaluator.py` |
| Equation tolerance | `agents/evaluator.py` |
| Test harness | `tests/` |
| D13 / B5 minor fixes | `agents/evaluator.py` |
| Excel Quality Report | `utils/excel_writer.py`, `agents/orchestrator.py` |
| Observability / check logging | `utils/observability.py` |

---

## Phased Implementation Plan — COMPLETE

### Phase 1: Foundation ✅ COMPLETE
**Goal:** Prepare data structures and isolated utilities.

1. **Equation tolerance refactor** — Tiered tolerance + materiality gate in `_run_equation_checks()`.
2. **Penalty ledger** — `PenaltyLedger` dataclass integrated into Coverage and Content checks.
3. **Minor fixes** — D13 materiality filter, B5 regex tightening.

**Deliverable:** All existing tests pass, equation tolerance handles small/large values correctly.

---

### Phase 2: Evaluator Rewrite ✅ COMPLETE
**Goal:** Implement the 4-section rubric, judge spec, and scoring aggregation.

1. **Section helper functions** — `_run_coverage_checks()`, `_run_format_checks()`, `_run_structure_checks()`, `_run_content_checks()` returning `SectionResult`.
2. **Programmatic scoring** — Each section computes a base 0-10 score from its checks.
3. **Judge integration** — `JUDGE_PROMPT` with strict JSON schema. Parse adjustments, clamp final scores.
4. **Weighted overall score** — A=20%, B=20%, C=30%, D=30%.
5. **Pass/fail logic** — Overall ≥ 6.0 + no hard-fail checks = PASS.

**Deliverable:** Evaluator outputs 4 scores + overall score + judge summary for every extraction.

---

### Phase 3: Retry Strategy ✅ COMPLETE
**Goal:** Make retries intelligent and targeted.

1. **Retry trigger conditions** — `Config.RETRY_MIN_OVERALL_SCORE`, `RETRY_HARD_FAIL_CHECKS`, `NEVER_RETRY_CHECKS`.
2. **Prompt mutation** — `_build_retry_context()` categorizes failures and generates targeted prompt addenda.
3. **Escalation ladder** — Attempt 2 adds OCR text; Attempt 3 switches to `Config.RETRY_EXTRACTION_MODEL`.
4. **Never-retry routing** — C7 and A3 block retry via `only_never_retry_fails` flag.

**Deliverable:** Retries are targeted, escalated, and never wasted on structural PDF issues.

---

### Phase 4: Excel Quality Report ✅ COMPLETE
**Goal:** Append the Quality Report tab to every Excel output.

1. **`_write_quality_report_sheet()`** — Full sheet layout with title, metadata, PASS/FAIL styling, score breakdown, check statuses, guardrail flags, feedback, retry history, and detailed findings.
2. **`report_metadata` plumbing** — `save_to_excel()` accepts `report_metadata`; `save_outputs()` builds it from `evaluation_result`.
3. **Edge cases** — Empty findings, no guardrail flags, categorization enabled/disabled.

**Deliverable:** Every `.xlsx` contains a "Quality Report" tab with production-grade formatting.

---

### Phase 5: Test Harness ✅ COMPLETE
**Goal:** Replace manual verification with automated tests.

1. **Fixture library** — `tests/fixtures/` directories created (`known_good`, `known_bad`, `edge_cases`).
2. **`test_evaluator.py`** — Unit tests for parse, precheck, equations, penalty ledger, retry context.
3. **`test_excel_report.py`** — Schema validation for Quality Report tab (sheet exists, PASS/FAIL cell, score cells).
4. **`test_pipeline.py`** — Integration test skeleton with `pytest.mark.skipif` for fixture PDFs.
5. **CI integration** — Fast unit tests run on every commit; slow/latency tests marked with skipif.

**Deliverable:** `pytest tests/test_evaluator.py tests/test_excel_report.py` runs green (37 tests).

---

### Phase 6: Production Hardening ✅ COMPLETE
**Goal:** Performance, observability, and guardrail toggles.

1. **`ENABLE_DEEP_CONTENT_CHECKS`** toggle — Disable label ground-truth (A6, D1-D3, D4) when latency is critical.
2. **Observability integration** — `log_check_outcomes()` logs all 43 individual check results to JSONL for dashboarding.
3. **Batch benchmarking** — Baseline established; latency target < 30s per evaluator run.
4. **Documentation** — This plan, `EVALUATION_METRICS.md`, and `GUARDRAILS.md` updated.

**Deliverable:** System is production-ready with full observability, batch performance validated, and documentation complete.

---

## Summary of Files Affected

| File | Phases Touching It | Lines Changed |
|------|-------------------|---------------|
| `agents/evaluator.py` | Phase 1, Phase 2, Phase 6 | ~1500 lines (major rewrite) |
| `agents/orchestrator.py` | Phase 3, Phase 4 | Retry logic + report metadata plumbing |
| `agents/extractor.py` | Phase 3 | Targeted prompt mutation + escalation ladder |
| `utils/excel_writer.py` | Phase 4 | Quality Report sheet writer |
| `utils/observability.py` | Phase 6 | `log_check_outcomes()` for 43 checks |
| `config.py` | Phase 1, Phase 3, Phase 6 | Tolerance, retry policy, deep-content toggle |
| `tests/test_evaluator.py` | Phase 5 | 37 unit tests covering rubric + retry context |
| `tests/test_excel_report.py` | Phase 5 | 3 tests for Quality Report schema |
| `tests/test_pipeline.py` | Phase 5 | Integration test skeleton |
| `docs/PRODUCTION_PLAN.md` | Phase 6 | This document |
| `docs/EVALUATION_METRICS.md` | Phase 6 | Complete rewrite for 4-section rubric |
| `docs/GUARDRAILS.md` | Phase 6 | Updated as-built status |
