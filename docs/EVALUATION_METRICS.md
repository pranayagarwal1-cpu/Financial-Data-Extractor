# Evaluation Metrics Reference

This document describes every metric captured by the system's two evaluators:

- **Extractor Evaluator** (`evaluator` agent) — validates financial statement extraction quality
- **Categorizer Evaluator** (`cat_evaluator` agent) — validates CoA categorization quality

Each metric is tagged with:
- **Agent** — the agent that produces or consumes it
- **Layer** — whether it comes from fast deterministic Pre-Checks, subjective LLM-as-Judge scoring, or raw Telemetry

---

## Extractor Evaluator (`evaluator`)

The extractor evaluator runs a two-layer quality gate on extracted financial statement JSON:
1. **Pre-Checks** — fast deterministic rules (no LLM call)
2. **LLM-as-Judge** — structured scoring prompt sent to a separate model
3. **Hard Override** — programmatic equation checks can force FAIL even if the LLM says PASS

| Metric | Agent | Layer | Definition |
|--------|-------|-------|------------|
| `has_sections` | evaluator | Pre-Check | Boolean — whether all required section keywords are present (e.g., ASSET / LIABILIT / EQUITY for Balance Sheet) |
| `missing_sections` | evaluator | Pre-Check | List of required section keywords not found in the extraction |
| `missing_value_ratio` | evaluator | Pre-Check | Proportion of null / empty values across all row values (0.0–1.0); >20 % is flagged |
| `numeric_parseability` | evaluator | Pre-Check | Score 0–10 based on what fraction of non-null values successfully parse to Decimal |
| `equation_passed` | evaluator | Pre-Check | Boolean from hard programmatic checks: BS balances, IS Gross Profit reconciles, CF Net Change reconciles |
| `equation_feedback` | evaluator | Pre-Check | Specific mismatch message if equation check fails (e.g., `Assets (X) ≠ Liab + Equity (Y)`) |
| `completeness` | evaluator | LLM Judge | 0–10: Are all required major sections present? (BS = 3, IS = 3, CF = 3). Critical — must be 10 to pass. |
| `data_integrity` | evaluator | LLM Judge | 0–10: Do subtotals reconcile with line items? Capped at ≤ 3 if programmatic check fails. |
| `period_consistency` | evaluator | LLM Judge | 0–10: Are the same fiscal periods used across all sections? |
| `format_validity` | evaluator | LLM Judge | 0–10: Is the JSON structure valid with all required fields (`title`, `periods`, `sections`, `rows`)? |
| `missing_values` | evaluator | LLM Judge | 0–10: Is the missing value ratio under the 20 % threshold? |
| `passed` | evaluator | LLM Judge | Boolean final verdict. True only if completeness = 10, format_validity = 10, avg ≥ 7, **and** programmatic checks pass. |
| `feedback` | evaluator | LLM Judge | Text explanation of issues or confirmation of quality; injected into retry extraction prompts. |
| `avg_score` | evaluator | Derived | Arithmetic mean of the 5 LLM scores; threshold ≥ 7. |
| `evaluator_duration_ms` | — | Telemetry | Wall-clock time for the evaluator node (pre-checks + LLM call + equation checks). |
| `eval_llm_duration_ms` | — | Telemetry | Time spent in the LLM-as-Judge call only. |
| `eval_llm_prompt_tokens` | — | Telemetry | Rough token estimate (chars / 4) for the evaluation prompt. |
| `eval_llm_response_tokens` | — | Telemetry | Rough token estimate for the JSON evaluation response. |

### Pass Criteria (all must be true)

1. `completeness == 10`
2. `format_validity == 10`
3. `avg_score >= 7`
4. `equation_passed == True` (hard override)

---

## Categorizer Evaluator (`cat_evaluator`)

The categorizer evaluator runs a two-layer quality gate on CoA-mapped P&L line items:
1. **Pre-Checks** — fast heuristic stats and learned-correction validation
2. **LLM-as-Judge** — structured scoring prompt with category-sanity rules
3. **Hard Override** — if a learned correction was ignored ≥ 2 times, force FAIL

| Metric | Agent | Layer | Definition |
|--------|-------|-------|------------|
| `total_line_items` | cat_evaluator | Pre-Check | Total rows across all sections (including headers and subtotals) |
| `postable_items` | cat_evaluator | Pre-Check | Rows that are not section headers or subtotals (items that should map to CoA) |
| `section_headers` | cat_evaluator | Pre-Check | Count of structural header rows excluded from mapping |
| `categorized_items` | cat_evaluator | Pre-Check | Postable items that received a CoA code |
| `high_conf_count` | cat_evaluator | Pre-Check | Postable items mapped with `confidence == "high"` |
| `needs_review` | cat_evaluator | Pre-Check | Items flagged `needs_review = True` by the categorizer |
| `coverage_rate` | cat_evaluator | Pre-Check | `categorized_items / postable_items` (0.0–1.0) |
| `high_conf_rate` | cat_evaluator | Pre-Check | `high_conf_count / categorized_items` (0.0–1.0) |
| `review_rate` | cat_evaluator | Pre-Check | `needs_review / total_line_items` (0.0–1.0) |
| `ignored_corrections` | cat_evaluator | Pre-Check | List of learned memory rules that were repeated despite prior correction |
| `ignored_count` | cat_evaluator | Pre-Check | Number of repeated mistakes found in memory |
| `coverage` | cat_evaluator | LLM Judge | 0–10: % of postable items mapped (10 if ≥ 90 %, 7 if ≥ 70 %, 5 if ≥ 50 %) |
| `confidence` | cat_evaluator | LLM Judge | 0–10: % of high-confidence matches (10 if ≥ 80 % high) |
| `category_sanity` | cat_evaluator | LLM Judge | 0–10: Hard rule violations (Revenue → 5xxx, COGS → 6xxx, OpEx → 7xxx, etc.) |
| `reasoning_consistency` | cat_evaluator | LLM Judge | 0–10: Does reasoning contradict the assigned account? |
| `learned_corrections` | cat_evaluator | LLM Judge | 0–10: Were prior memory corrections respected? 0 if any ignored |
| `review_burden` | cat_evaluator | LLM Judge | 0–10: % of items needing human review (10 if < 10 %) |
| `format_validity` | cat_evaluator | LLM Judge | 0–10: Is JSON valid with required fields (`is_split`, `split_accounts`) |
| `passed` | cat_evaluator | LLM Judge | Boolean — true if avg ≥ 6, coverage ≥ 7, format_validity = 10, sanity ≥ 5, learned = 10 |
| `violations` | cat_evaluator | LLM Judge | List of specific account mismatches found (e.g., "Revenue → 7700") |
| `feedback` | cat_evaluator | LLM Judge | Text explanation of issues or confirmation |
| `cat_evaluator_duration_ms` | — | Telemetry | Wall-clock time for the cat evaluator node |
| `cat_eval_llm_duration_ms` | — | Telemetry | Time spent in the LLM-as-Judge call only |
| `cat_eval_llm_prompt_tokens` | — | Telemetry | Rough token estimate for the cat evaluation prompt |
| `cat_eval_llm_response_tokens` | — | Telemetry | Rough token estimate for the cat evaluation JSON response |

### Pass Criteria (all must be true)

1. `avg_score >= 6`
2. `coverage >= 7`
3. `format_validity == 10`
4. `category_sanity >= 5`
5. `learned_corrections == 10`
6. **Hard override**: if a learned correction was ignored ≥ 2 times, force fail regardless of LLM verdict

---

## Layer Definitions

- **Pre-Check** — Fast, deterministic, code-based sanity checks that run *before* the LLM call. They catch obvious errors without token cost.
- **LLM Judge** — Subjective scoring produced by sending a structured prompt to an LLM. Costs tokens and latency.
- **Derived** — Computed from other metrics (e.g., arithmetic mean).
- **Telemetry** — Observability data (timing, token estimates) logged to JSONL / disk for operational monitoring. Not used in pass/fail logic.
