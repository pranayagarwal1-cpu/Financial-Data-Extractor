# Guardrails & Safety Architecture

**Document status:** Living document — updated as guardrails are implemented.  
**Last updated:** 2026-05-10

---

## 1. Current Guardrails (As-Built)

### Retry & Recovery

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **Extraction retry cap** | `config.py:38` / `agents/orchestrator.py:170` | Max 2 re-extraction attempts (3 total). After max, terminates without saving. |
| **Categorization retry cap** | `config.py:39` / `agents/orchestrator.py:222` | Max 2 re-categorization attempts. After max, saves anyway. |
| **Selective retry** | `agents/extractor.py:111-127` | On retry, only re-extracts statement types that failed evaluation. Passed types preserved. |
| **Per-page fault tolerance** | `agents/extractor.py:177-180` | Single page extraction failure returns `None`; merger continues with remaining pages. |
| **Targeted retry prompts** | `agents/evaluator.py:111-172` | `_build_retry_context()` categorizes failures (missed rows, equation errors, sign errors, JSON invalid) and generates targeted prompt addenda. |
| **Retry escalation ladder** | `agents/extractor.py:74-95` | Attempt 2: injects OCR ground-truth. Attempt 3: switches to `Config.RETRY_EXTRACTION_MODEL`. |
| **Never-retry routing** | `agents/orchestrator.py:170-215` | Structural failures (C7 orientation, A3 page coverage) block retry — retry would not fix them. |
| **Batch retry with backoff** | `agents/categorizer.py:383-396` | Failed categorization batches retried sequentially with 5-second cooldown. |
| **Evaluator fallback** | `agents/evaluator.py:1255-1267` | If judge response is unparseable, falls back to adjustment=0. Never blocks pipeline. |

### Quality Checks

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **Numeric precheck hard-fail** | `agents/evaluator.py:125-143` | If >50% of values are unparsable, forces evaluation `passed=False` before LLM judge is called. |
| **Programmatic equation checks** | `agents/evaluator.py:603-675` | Balance sheet, income statement, and cash flow reconciliation with tiered tolerance + materiality gate. Hard-overrides LLM judge when equations don't balance. |
| **4-section rubric (43 checks)** | `agents/evaluator.py:783-1209` | Coverage (7), Format (7), Structure (14), Content (15). Each check produces PASS/FAIL/ADVISORY. |
| **Penalty ledger** | `agents/evaluator.py:71-89` | Prevents double-penalizing the same error across Coverage and Content sections. |
| **LLM-as-Judge bounded adjustments** | `agents/evaluator.py:29-63` | Judge can adjust each section score by ±1.0 max. Cannot override hard-fail programmatic checks. |
| **Completeness gate** | `agents/evaluator.py:801-807` | A1 (required sections) is a hard-fail. Missing sections block passing regardless of overall score. |
| **Ambiguity detection** | `agents/categorizer.py:76-82` | Scans LLM reasoning text for uncertainty keywords ("could be", "unclear", etc.) and auto-flags `needs_review=True`. |
| **Confidence scoring** | `agents/categorizer.py:495-501` | Categorization matches scored `high/medium/low/unmatched`; low-confidence items surfaced in UI for human review. |
| **Learned-correction enforcement** | `agents/cat_evaluator.py:396-403` | If a previously-learned correction is ignored 2+ times, forces categorization evaluation to FAIL. |
| **Hallucination ground-truth check** | `agents/evaluator.py:1095-1102` | Verifies subtotal/total values against source page text. Configurable via `ENABLE_DEEP_CONTENT_CHECKS`. |
| **Quality degradation tracker** | `agents/evaluator.py:1373-1398` / `utils/guardrails.py` | Cross-run tracking of evaluation scores. Detects model drift and flags `quality_degraded` after repeated low scores. |
| **Deep content check toggle** | `config.py:55-57` | `ENABLE_DEEP_CONTENT_CHECKS` (default True) can disable expensive label/value ground-truth checks when latency is critical. |

### Input Validation

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **File type restriction** | `ui/sidebar.py:56` / `main.py:144` | UI restricts uploads to `.pdf`; CLI globs `*.pdf`. |
| **PDF path sanitization** | `utils/pdf_utils.py:7-14` | Resolves path, checks `.exists()` and `.is_file()` before passing to `pdfinfo`/`pdftoppm`. Prevents command injection. |
| **Text layer detection** | `utils/llm_detector.py:196-201` | Checks if extracted text length > 100 chars across all pages. If not, falls back to VLM-based image detection. |
| **Page number validation** | `utils/llm_detector.py:295-299` | Validates LLM-returned page numbers are within `1 <= p <= total_pages`. |
| **Pydantic structural validation** | `utils/vlm_utils.py:309-310` | `StatementData.from_vlm_dict()` validates VLM JSON output against schema before downstream propagation. |

### Observability & Cost Tracking

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **Per-run metrics** | `utils/observability.py` | Tracks duration, LLM calls, retries, token counts, cost per model, evaluation scores. |
| **Cost logging** | `utils/observability.py:25-63` | Model-specific cost rates (Anthropic priced, Ollama = $0). Per-model usage breakdown saved to `output/metrics/*.json`. |
| **Per-run budget logging** | `utils/observability.py:256-267` | Accumulates `total_prompt_tokens`, `total_completion_tokens`, `total_cost_usd` per run. |
| **Check outcome logging** | `utils/observability.py:320-333` | `log_check_outcomes()` writes all 43 individual check results to daily JSONL for dashboarding. |
| **Freemium cap** | `utils/freemium.py` | Hard limit of 2 free extractions per month per email address. |

### Missing / Not Implemented

| Mechanism | Status | Risk |
|-----------|--------|------|
| **LLM timeout** | ❌ None | Hung Ollama/Anthropic calls block workflow indefinitely. |
| **Circuit breaker** | ❌ None | Repeated LLM failures cascade without fast-fail or fallback. |
| **Rate limiting** | ❌ None | `ThreadPoolExecutor` spawns unlimited workers based on statement type / batch count. |
| **Cost hard cap** | ⚠️ Config only | `GUARDRAIL_MAX_COST_PER_RUN` and `GUARDRAIL_MAX_TOKENS_PER_RUN` are tracked but not enforced. Runaway Anthropic retries could rack up charges. |
| **PII redaction** | ❌ None | No detection or redaction of SSNs, emails, account numbers in prompts or logs. |
| **PDF size / page limits** | ❌ None | No max file size, max page count, or max image dimension checks. |
| **Concurrency cap** | ❌ None | No limit on parallel workflow invocations. |
| **Data integrity escalation** | ⚠️ Partial | Equation checks fail evaluation but never hard-abort the run when diff is severe (>10%). |

---

## 2. Proposed Guardrails (Phased)

### Phase A — "Keep the System Alive" (Priority: High)

Focus: prevent runaway costs, catch bad inputs early, and detect when the LLM is producing garbage.

#### A1. Cost Guardrail (Hard Cap)

**What it does:** Enforce a per-run token and dollar budget. Warn at 80%, hard-stop at 100%.

**Where:** `utils/llm_client.py` (pre-call check), `utils/observability.py` (accumulator hook).

**Trigger:**
- Per-run tokens > `GUARDRAIL_MAX_TOKENS_PER_RUN` (default 200,000)
- Per-run cost > `GUARDRAIL_MAX_COST_PER_RUN` (default $3.00)

**Action:**
- At 80%: emit warning, switch to cheaper model for remaining calls.
- At 100%: raise `CostLimitExceededError`. Workflow saves whatever data exists with `cost_limited: true` flag.

**Config:**
```python
GUARDRAIL_MAX_TOKENS_PER_RUN = int(os.getenv("GUARDRAIL_MAX_TOKENS_PER_RUN", "200000"))
GUARDRAIL_MAX_COST_PER_RUN = float(os.getenv("GUARDRAIL_MAX_COST_PER_RUN", "3.00"))
```

**Status:** Config values exist but enforcement is not yet implemented.

---

#### A2. Quality Tracker (Degradation Detection) ✅ Implemented

**What it does:** Maintain a sliding window of evaluation scores. Detect when the model is producing consistently low-quality output.

**Where:** `agents/evaluator.py:1373-1398`, `utils/guardrails.py`.

**Trigger:**
- Run-level: average evaluation score < 5.0 for 2 consecutive attempts on same PDF.
- Global-level: 3 consecutive runs (any PDFs) with average score < 5.0.

**Action:**
- Run-level: switch to higher-quality retry model (`Config.RETRY_EXTRACTION_MODEL`).
- Global-level: disable retry loop, save raw data with `guardrail_flags: ["quality_degraded"]`.

**Reset:** Global tracker resets after 2 consecutive runs with average score >= 7.0.

---

#### A3. Input Validator (Fail-Fast for Bad PDFs)

**What it does:** Validate PDF health before any LLM calls are attempted.

**Where:** `agents/orchestrator.py` (top of `orchestrator_node()`).

**Trigger:**
- File size = 0 or > 100 MB
- `pdfplumber.open()` raises `PDFSyntaxError`
- Total pages = 0
- Text layer absent (`total_text_length < 100`) AND VLM fallback also returns empty

**Action:** Immediate fail-fast. Returns `{"error_message": "Malformed input: <reason>"}`. No LLM calls.

**Status:** Basic file existence + type validation exists. Size/page limits not yet enforced.

---

#### A4. Concurrency Limiter

**What it does:** Cap parallel workflow invocations and internal ThreadPoolExecutor workers.

**Where:** `main.py` (workflow-level), `agents/extractor.py`, `agents/categorizer.py`.

**Trigger:** `active_workflows >= GUARDRAIL_MAX_CONCURRENT_RUNS` (default 4).

**Action:**
- Workflow-level: queue or reject with `"System is at maximum concurrency. Please try again later."`
- Node-level: cap `ThreadPoolExecutor(max_workers)` to 3 (extractor) / 4 (categorizer).

**Status:** `GUARDRAIL_MAX_CONCURRENT_RUNS` exists in config but is not enforced.

---

### Phase B — "Keep Data Safe" (Priority: Medium)

Focus: protect sensitive data in prompts/logs, and catch fabricated values.

#### B1. PII Guardrail (Redaction)

**What it does:** Detect and redact SSNs, emails, phone numbers, bank account numbers from prompts before they leave the local machine, and from logs before they hit disk.

**Where:** `utils/llm_client.py` (pre-send sanitization), `utils/observability.py` (pre-log redaction).

**Trigger:** Regex match in prompt text or log payload.

**Action:** Replace matches with `[REDACTED-SSN]`, `[REDACTED-EMAIL]`, etc. Never blocks execution — always-on sanitizer.

**Risk assessment:** Low-medium. Only financial statement pages (not full PDF) are sent to LLMs. But account numbers, tax IDs, or phone numbers can still appear in statement footnotes.

**Status:** Not implemented.

---

#### B2. Hallucination Guardrail (Ground-Truth Check) ✅ Implemented

**What it does:** After extraction, verify that subtotal/total values and labels fuzzy-match text extracted from the source PDF pages.

**Where:** `agents/evaluator.py:1095-1119`. `agents/extractor.py` stores source page texts in state (`page_texts`).

**Trigger:** > 3 subtotal values or > 3 labels that do not appear in source page text.

**Action:**
- Forces evaluation `passed=False`.
- Injects anti-hallucination prompt on retry.
- If max retries reached: save with `guardrail_flags: ["hallucination_warning"]`.
- **Toggle:** `Config.ENABLE_DEEP_CONTENT_CHECKS` (default True) controls whether this runs.

---

#### B3. Data Integrity Escalation (Hard Fail)

**What it does:** When accounting equation checks show a severe mismatch, abort the run instead of saving bad data.

**Where:** `agents/evaluator.py`, `agents/orchestrator.py` (`should_retry()`).

**Trigger:** After `MAX_RETRIES`, balance sheet diff > 10% of expected total.

**Action:** Route to `guardrail_sink` node instead of `save_outputs`. No output files produced. Observability run ends `success=False` with `data_quality_severity: "critical"`.

**Status:** Not implemented. Currently equation failures set `passed=False` but still allow saving after max retries.

---

### Phase C — "Resilience" (Priority: Low)

Focus: handle LLM backend outages and timeouts.

#### C1. Model Circuit Breaker with Fallback

**What it does:** Fast-fail when an LLM backend is repeatedly failing, with automatic fallback to alternate backend.

**Where:** `utils/llm_client.py`.

**Trigger:** 3 consecutive exceptions from same backend (`ConnectionError`, `TimeoutError`, rate-limit 429/502/503).

**Action:**
- Ollama OPEN → fallback to Anthropic.
- Anthropic OPEN → fallback to Ollama.
- Both OPEN → raise `CircuitBreakerOpenError`, route to `guardrail_sink`.

**Reset:** Ollama 30s, Anthropic 300s. HALF_OPEN allows 1 probe call.

**Status:** Not implemented.

---

#### C2. Request Timeout

**What it does:** Inject explicit request-level timeouts on all LLM calls.

**Where:** `utils/ollama_client.py`, `utils/anthropic_client.py`.

**Action:** `ollama.Client(timeout=120)`, `Anthropic(timeout=120)`. Raises `TimeoutError` if exceeded.

**Status:** Not implemented.

---

## 3. Decision Log

| Date | Decision | Rationale |
|------|----------|----------|
| 2026-05-09 | Ollama downtime deprioritized | User runs local Ollama; primary concern is model quality degradation, not availability. |
| 2026-05-09 | Anthropic cost guardrail prioritized | Only cloud backend with metered billing; Ollama is $0. |
| 2026-05-09 | PII guardrail in Phase B (not A) | Financial statement pages are a subset of the full PDF; lower exposure than full-document upload systems. |
| 2026-05-10 | Quality tracker (A2) promoted to As-Built | Implemented as part of Phase 6 production hardening. Cross-run score tracking with `guardrail_flags: ["quality_degraded"]`. |
| 2026-05-10 | Hallucination guardrail (B2) upgraded | A6 + D1-D4 ground-truth checks fully implemented with `ENABLE_DEEP_CONTENT_CHECKS` toggle. |
| 2026-05-10 | Targeted retry + escalation ladder added | Never-retry routing prevents wasted retries on structural PDF issues. Prompt mutation based on failure category. |
