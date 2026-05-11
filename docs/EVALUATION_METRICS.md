# Evaluation Metrics Reference

**Document status:** Updated for 4-section rubric (Coverage, Format, Structure, Content)  
**Last updated:** 2026-05-10

---

## Overview

The extractor evaluator runs a **three-layer quality gate** on extracted financial statement JSON:

1. **Programmatic Pre-Checks** — 43 fast deterministic rules (no LLM call). These produce 4 section scores (0–10) and a set of `PASS` / `FAIL` / `ADVISORY` findings.
2. **LLM-as-Judge** — Bounded qualitative adjustment (±1.0 max per section). Catches things rules miss without overriding them.
3. **Hard Override** — Certain programmatic failures (missing sections, invalid JSON, equation mismatches) can force `passed=False` regardless of overall score.

---

## Metric Reference

### Programmatic Checks (43 total)

Each check produces:
- `check_id` — e.g. `A1`, `B4`, `C1-C3`, `D7`
- `status` — `PASS` | `FAIL` | `ADVISORY`
- `message` — Human-readable description

| Check ID | Section | Description | Hard Fail? |
|----------|---------|-------------|------------|
| A1 | Coverage | Required sections present | Yes |
| A2 | Coverage | Missing value ratio < 20% | No |
| A3 | Coverage | Page coverage (all detected pages found) | No |
| A4 | Coverage | Row count sanity (not extreme outlier) | Yes (if < 5 rows) |
| A5 | Coverage | Fields present but not extracted | No |
| A6 | Coverage | Hallucinated fields (labels not in OCR) | Yes (if > 2) |
| A7 | Coverage | Comparative period integrity | No |
| B1 | Format | Numeric parseability ≥ 95% | Yes (if < 50%) |
| B2 | Format | JSON structure valid | Yes |
| B3 | Format | Period consistency across sections | No |
| B4 | Format | Column alignment (len(values) == len(periods)) | Yes |
| B5 | Format | Period labels are dates, not ratios | No |
| B6 | Format | Currency format consistency | No |
| B7 | Format | Date format normalization | No |
| C1-C3 | Structure | Accounting equations balance (BS/IS/CF) | Yes |
| C4 | Structure | Section sum reconciliation | Yes |
| C5 | Structure | Indent normalization | No |
| C6 | Structure | Hierarchy validation | No |
| C7 | Structure | Page orientation events | No |
| C8 | Structure | Table spanning pages | No |
| C9 | Structure | Multicolumn layout safety | No |
| C10 | Structure | Header/footer bleed | No |
| C11 | Structure | Section boundary coherence | No |
| C12 | Structure | Duplicate row detection | No |
| C13 | Structure | Row order integrity | No |
| C14 | Structure | Cross-statement reconciliation | No |
| D1-D3 | Content | Hallucination / ground-truth | Yes (if > thresholds) |
| D4 | Content | Label fuzzy accuracy | No |
| D5 | Content | Value range sanity | No |
| D6 | Content | Sign consistency (parentheses = negative) | No |
| D7 | Content | Truncated fields | No (always ADVISORY) |
| D8 | Content | Merged cells detection | No |
| D9 | Content | Unit scale errors | No |
| D10 | Content | OCR artifacts in labels | No |
| D11 | Content | Decimal precision drift | No |
| D12 | Content | VLM interpolation (invented subtotals) | No |
| D13 | Content | Duplicate values across periods | No |
| D14 | Content | Materiality scoring | No |
| D15 | Content | Row order integrity (children before subtotal) | No |

---

### Scoring Metrics

| Metric | Source | Definition |
|--------|--------|------------|
| `coverage_score` | Programmatic | 0–10 based on A1–A7 outcomes |
| `format_score` | Programmatic | 0–10 based on B1–B7 outcomes |
| `structure_score` | Programmatic | 0–10 based on C1–C14 outcomes |
| `content_score` | Programmatic | 0–10 based on D1–D15 outcomes |
| `coverage_adj` | LLM Judge | Bounded adjustment ∈ [-1.0, 1.0] |
| `format_adj` | LLM Judge | Bounded adjustment ∈ [-1.0, 1.0] |
| `structure_adj` | LLM Judge | Bounded adjustment ∈ [-1.0, 1.0] |
| `content_adj` | LLM Judge | Bounded adjustment ∈ [-1.0, 1.0] |
| `final_coverage` | Derived | `clamp(programmatic + adj, 0, 10)` |
| `final_format` | Derived | `clamp(programmatic + adj, 0, 10)` |
| `final_structure` | Derived | `clamp(programmatic + adj, 0, 10)` |
| `final_content` | Derived | `clamp(programmatic + adj, 0, 10)` |
| `overall_score` | Derived | `0.20*A + 0.20*B + 0.30*C + 0.30*D` |
| `passed` | Derived | `overall >= 6.0 and not any_hard_fail` |
| `retry_context` | Derived | Structured dict with failure categories, prompt addenda, and retry recommendation |

---

### Quality Report Metrics

| Metric | Source | Definition |
|--------|--------|------------|
| `overall_status` | Derived | `"PASS"` if `passed == True`, else `"FAIL"` |
| `guardrail_flags` | Guardrails | List of active flags (`quality_degraded`, `hallucination_warning`, `input_validation_failed`) |
| `feedback` | Derived | Human-readable summary from failed findings + judge summary |
| `retry_count` | Telemetry | Number of extraction attempts used |
| `failed_findings` | Derived | List of `{check_id, status, message}` for all FAIL/ADVISORY checks |
| `findings` | Derived | List of all 43 check outcomes |

---

### Telemetry Metrics

| Metric | Source | Definition |
|--------|--------|------------|
| `evaluator_duration_ms` | Telemetry | Wall-clock time for the evaluator node (pre-checks + LLM call + clamping) |
| `eval_llm_duration_ms` | Telemetry | Time spent in the LLM-as-Judge call only |
| `eval_llm_prompt_tokens` | Telemetry | Rough token estimate (chars / 4) for the evaluation prompt |
| `eval_llm_response_tokens` | Telemetry | Rough token estimate for the JSON evaluation response |
| `check_outcomes` | Telemetry | JSONL event with all 43 check statuses per statement per run |

---

### Pass Criteria (all must be true)

1. `overall_score >= 6.0`
2. No hard-fail check failed:
   - A1 (required sections missing)
   - B2 (JSON invalid)
   - C1–C3 (equation failures)

When `Config.DISABLE_GUARDRAILS == True`, hard-fail checks are ignored and only the overall score threshold applies.

---

## Layer Definitions

- **Programmatic Pre-Check** — Fast, deterministic, code-based checks that run *before* the LLM call. They catch obvious errors without token cost.
- **LLM Judge** — Subjective scoring produced by sending a structured prompt to an LLM. Provides bounded adjustments [-1.0, +1.0] per section. Costs tokens and latency.
- **Derived** — Computed from other metrics (e.g. weighted average, clamped scores).
- **Telemetry** — Observability data (timing, token estimates, check outcomes) logged to JSONL / disk for operational monitoring.

---

## Configuration Toggles

| Toggle | Default | Effect |
|--------|---------|--------|
| `Config.DISABLE_GUARDRAILS` | `false` | When `true`, skips input validation, quality tracking, and hard-fail checks. |
| `Config.ENABLE_DEEP_CONTENT_CHECKS` | `true` | When `false`, skips A6, D1-D3, and D4 (label/value ground-truth checks). Reduces latency by ~200-500ms on scanned PDFs. |
| `Config.RETRY_EXTRACTION_MODEL` | `None` | If set, switches to this model on retry attempt 3. Used for escalation to higher-capability models. |
| `Config.AUTO_CORRECT_ORIENTATION` | `true` | When `true`, auto-rotates inverted/scanned pages before extraction. |
