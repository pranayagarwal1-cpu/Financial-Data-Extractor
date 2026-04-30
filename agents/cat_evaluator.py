"""
Categorizer Evaluator Agent (LLM-as-Judge) - Evaluates categorization quality.

Checks:
- Coverage: % of line items successfully mapped to CoA
- Confidence: % of high-confidence matches
- Category Sanity: revenue items → 5xxx, expense items → 6xxx/7xxx
- Review Burden: inverse of % needing human review
- Format Validity: valid JSON with categorization fields
"""

import json
import logging
import time
from typing import Dict, List

from utils.ollama_client import chat

from config import Config


CAT_EVALUATION_PROMPT = """You are a senior veterinary practice accountant evaluating the quality of CoA (Chart of Accounts) \
categorization for a veterinary practice P&L statement.

Evaluate the categorized line items against these criteria:

1. **Coverage**: Are most line items mapped to a CoA account? (section headers excluded)
2. **Confidence**: Are most matches high-confidence?
3. **Category Sanity**: Check the following — any violation scores 0 immediately:
   - Revenue items → 5xxx only. Never 6xxx, 7xxx, or 1xxx–4xxx.
   - COGS items → 6xxx only. Never 7xxx.
   - Operating expense items → 7xxx only. Never 6xxx.
   - Amortization → must be 8050, never 7700.
   - Interest expense → must be 9080/9090/9095, never 7700.
   - Income taxes → must be 9100/9200/9300, never 7700.
   - Rent/Apartment revenue → must be 9010, not 9000.
   - Interest/Dividend revenue → must be 9020, not 9000.
   - Gains on asset sales → must be 9030, not 9000.
   - Balance sheet accounts (1xxx–4xxx) → NEVER valid for any P&L item.
4. **Reasoning Consistency**: Does the `reasoning` field contradict the assigned account?
   Look for items where reasoning mentions a better account than what was assigned.
   (e.g., reasoning says "should be 9080" but account_id is "7700" → flag it)
5. **Learned Corrections**: Did the LLM ignore any previously-learned corrections?
   If a sample mapping repeats a mistake that was corrected in a prior run,
   the category_sanity score must be 0 regardless of other metrics.
6. **Review Burden**: Are too many items flagged for human review?

Here is the categorization summary:
{summary}

Here are sample mappings (up to 50):
{sample_mappings}

Respond with ONLY valid JSON:
{{
  "scores": {{
    "coverage": <0-10>,
    "confidence": <0-10>,
    "category_sanity": <0-10>,
    "reasoning_consistency": <0-10>,
    "learned_corrections": <0-10>,
    "review_burden": <0-10>,
    "format_validity": <0-10>
  }},
  "passed": <true/false>,
  "violations": ["list any specific account mismatches found"],
  "feedback": "<brief explanation>"
}}

Scoring:
- coverage: 10 if ≥90% items mapped, 7 if ≥70%, 5 if ≥50%
- confidence: 10 if ≥80% high-confidence, 7 if ≥60%, 5 if ≥40%
- category_sanity: 10 if no violations above, 5 if 1–2 violations, 0 if any balance sheet account used or learned correction ignored
- reasoning_consistency: 10 if reasoning always matches account_id, deduct 2 per contradiction found
- learned_corrections: 10 if no prior corrections were ignored, 0 if any learned correction was repeated
- review_burden: 10 if <10% need review, 7 if <20%, 5 if <30%
- format_validity: 10 if valid JSON with all required fields including is_split and split_accounts

PASS if: average ≥ 6, coverage ≥ 7, format_validity = 10, category_sanity ≥ 5, learned_corrections = 10
"""


def _extract_sample_mappings(categorized_data: dict, limit: int = 20) -> List[dict]:
    """Extract a sample of mappings for the LLM to evaluate."""
    samples = []
    for section in categorized_data.get("sections", []):
        for row in section.get("rows", []):
            cat = row.get("categorization")
            if cat and cat.get("coa_code"):
                samples.append({
                    "label": row.get("label", ""),
                    "section": section.get("name", ""),
                    "coa_code": cat.get("coa_code"),
                    "coa_name": cat.get("coa_name"),
                    "coa_category": cat.get("coa_category"),
                    "confidence": cat.get("confidence"),
                    "match_type": cat.get("match_type"),
                    "reasoning": cat.get("reasoning", ""),
                    "is_split": cat.get("is_split", False),
                    "split_accounts": cat.get("split_accounts", []),
                })
            elif row.get("line_type") == "section_header":
                samples.append({
                    "label": row.get("label", ""),
                    "section": section.get("name", ""),
                    "type": "section_header (no account)",
                })
            elif not row.get("is_subtotal") and not row.get("line_type"):
                samples.append({
                    "label": row.get("label", ""),
                    "section": section.get("name", ""),
                    "coa_code": None,
                    "status": "UNCATEGORIZED",
                })
    return samples[:limit]


def _check_ignored_corrections(categorized_data: dict, practice_id: str) -> list:
    """Check if any learned memory corrections were ignored by the LLM."""
    if not practice_id:
        return []

    from utils.memory_manager import load_memory_rules
    rules = load_memory_rules(practice_id)
    if not rules:
        return []

    ignored = []
    rules_by_label = {}
    for rule in rules:
        key = rule.label.lower()
        rules_by_label.setdefault(key, []).append(rule)

    for section in categorized_data.get("sections", []):
        section_name = section.get("name", "")
        for row in section.get("rows", []):
            cat = row.get("categorization", {})
            if not cat or not cat.get("coa_code"):
                continue

            label = row.get("label", "").lower()
            assigned_code = str(cat.get("coa_code", ""))

            for rule in rules_by_label.get(label, []):
                # Check if the assigned code matches the WRONG code from memory
                if assigned_code == rule.wrong_code:
                    ignored.append({
                        "label": row.get("label", ""),
                        "section": section_name,
                        "assigned": assigned_code,
                        "expected": rule.correct_code,
                        "expected_name": rule.correct_name,
                        "count": rule.count,
                    })

    return ignored


def _run_heuristic_prechecks(
    categorized_data: dict,
    summary_stats: dict,
    practice_id: str = None,
) -> dict:
    """Run fast heuristic pre-checks before LLM evaluation."""
    total = summary_stats.get("total_line_items", 0)
    auto = summary_stats.get("auto_categorized", 0)
    llm_matched = summary_stats.get("llm_matched", 0)
    needs_review = summary_stats.get("needs_review", 0)

    total_categorized = auto + llm_matched

    # Count section headers separately
    section_headers = 0
    postable_items = 0
    categorized_items = 0
    high_conf_count = 0
    total_mapped = 0

    for section in categorized_data.get("sections", []):
        for row in section.get("rows", []):
            if row.get("is_subtotal"):
                continue
            lt = row.get("line_type", "")
            cat = row.get("categorization")
            if lt == "section_header":
                section_headers += 1
                continue
            postable_items += 1
            if cat and cat.get("coa_code"):
                categorized_items += 1
                total_mapped += 1
                if cat.get("confidence") == "high":
                    high_conf_count += 1

    # Coverage: % of postable items that got a CoA code
    coverage_rate = categorized_items / postable_items if postable_items > 0 else 0
    high_conf_rate = high_conf_count / total_mapped if total_mapped > 0 else 0
    review_rate = needs_review / total if total > 0 else 0

    # Check for ignored learned corrections
    ignored_corrections = _check_ignored_corrections(categorized_data, practice_id)

    return {
        "total_line_items": total,
        "postable_items": postable_items,
        "section_headers": section_headers,
        "categorized_items": categorized_items,
        "high_conf_count": high_conf_count,
        "needs_review": needs_review,
        "coverage_rate": round(coverage_rate, 3),
        "high_conf_rate": round(high_conf_rate, 3),
        "review_rate": round(review_rate, 3),
        "ignored_corrections": ignored_corrections,
        "ignored_count": len(ignored_corrections),
    }


def cat_evaluator_node(state: dict) -> dict:
    """
    Evaluate the quality of CoA categorization.

    Uses heuristics + LLM-as-Judge to score categorization quality
    and determine if re-categorization is needed.

    Args:
        state: Current workflow state with categorized_data, categorization_summary

    Returns:
        Updated state with cat_evaluation_result
    """
    from utils.observability import get_observability

    from pathlib import Path
    obs = get_observability()
    run_id = state.get("run_id")
    start_time = time.time()
    pdf_path = state.get("input_pdf", "")
    practice_id = Path(pdf_path).stem if pdf_path else None

    categorized_data = state.get("categorized_data", {})
    summary_stats = state.get("categorization_summary", {})

    if not categorized_data or not summary_stats:
        return {"cat_evaluation_result": {}}

    evaluation_results = {}

    for st_key, data in categorized_data.items():
        st_name = st_key.value if hasattr(st_key, 'value') else str(st_key)

        # Only evaluate income statement (only one that gets categorized)
        if st_name != "income_statement":
            evaluation_results[st_name] = {
                "passed": True,
                "feedback": "Not categorized (no evaluation needed)",
                "scores": {},
                "heuristics": {},
            }
            continue

        logging.info(f"Evaluating categorization quality...")
        print(f"\n🔍 Evaluating Categorization Quality...")

        try:
            # Layer 1: Heuristic pre-checks
            heuristics = _run_heuristic_prechecks(data, summary_stats, practice_id)

            print(f"   - Postable items: {heuristics['postable_items']}")
            print(f"   - Section headers: {heuristics['section_headers']}")
            print(f"   - Coverage rate: {heuristics['coverage_rate']:.1%}")
            print(f"   - High confidence rate: {heuristics['high_conf_rate']:.1%}")
            print(f"   - Review rate: {heuristics['review_rate']:.1%}")

            if heuristics["ignored_count"] > 0:
                print(f"   ⚠️  Ignored corrections: {heuristics['ignored_count']}")
                for ign in heuristics["ignored_corrections"]:
                    print(f"      - {ign['label']} → assigned {ign['assigned']} (expected {ign['expected']})")

            # If nothing to evaluate, skip
            if heuristics["postable_items"] == 0:
                evaluation_results[st_name] = {
                    "passed": True,
                    "feedback": "No postable items to categorize",
                    "scores": {},
                    "heuristics": heuristics,
                }
                continue

            # Layer 2: LLM semantic evaluation
            sample_mappings = _extract_sample_mappings(data, limit=50)

            summary_for_prompt = json.dumps({
                "total_line_items": heuristics["total_line_items"],
                "section_headers": heuristics["section_headers"],
                "postable_items": heuristics["postable_items"],
                "categorized": heuristics["categorized_items"],
                "uncategorized": heuristics["postable_items"] - heuristics["categorized_items"],
                "high_confidence": heuristics["high_conf_count"],
                "needs_review": heuristics["needs_review"],
                "coverage_rate": f"{heuristics['coverage_rate']:.1%}",
            }, indent=2)

            prompt = CAT_EVALUATION_PROMPT.format(
                summary=summary_for_prompt,
                sample_mappings=json.dumps(sample_mappings, indent=2),
            )

            llm_start = time.time()
            response = chat(
                model=Config.CAT_EVALUATION_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            llm_duration = (time.time() - llm_start) * 1000
            obs.log_llm_call(
                model=Config.CAT_EVALUATION_MODEL,
                duration_ms=llm_duration,
                prompt=prompt,
                response=response["message"]["content"],
                run_id=run_id,
            )

            # Parse LLM response
            content = response["message"]["content"].strip()

            # Clean deepseek-r1 </think>... response blocks
            if "</think>" in content:
                content = content.split(" response")[-1].strip()

            # Clean markdown fences
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.rstrip("`").strip()

            evaluation = json.loads(content)

            status = "✅ PASSED" if evaluation.get("passed") else "❌ FAILED"
            logging.info(f"Categorization evaluation: {status}")
            print(f"   - Evaluation: {status}")
            print(f"   - Feedback: {evaluation.get('feedback', 'No feedback')}")

            avg_score = (
                sum(evaluation.get("scores", {}).values())
                / max(len(evaluation.get("scores", {})), 1)
            )
            obs.log_evaluation_score(
                statement_type="categorization",
                score=round(avg_score, 2),
                details=evaluation.get("scores", {}),
                run_id=run_id,
            )

            # Override: fail if learned corrections were ignored (count >= 2)
            hard_fail = any(
                ign["count"] >= 2 for ign in heuristics.get("ignored_corrections", [])
            )
            passed = evaluation.get("passed", False) and not hard_fail

            if hard_fail and evaluation.get("passed"):
                print(f"   ⚠️  Forcing FAIL: learned correction ignored {hard_fail} time(s)")

            evaluation_results[st_name] = {
                "passed": passed,
                "feedback": evaluation.get("feedback", ""),
                "scores": evaluation.get("scores", {}),
                "heuristics": heuristics,
            }

        except json.JSONDecodeError as e:
            logging.error(f"Error parsing cat evaluation: {e}")
            print(f"   ⚠️  Error parsing evaluation: {e}")
            # Fall back to heuristic-only pass/fail
            heuristics = _run_heuristic_prechecks(data, summary_stats, practice_id)
            passed = heuristics["coverage_rate"] >= 0.7 and heuristics["ignored_count"] == 0
            evaluation_results[st_name] = {
                "passed": passed,
                "feedback": f"Heuristic-only: coverage={heuristics['coverage_rate']:.1%}, ignored={heuristics['ignored_count']} (LLM parse failed)",
                "scores": {},
                "heuristics": heuristics,
            }

        except Exception as e:
            logging.error(f"Cat evaluation error: {e}")
            print(f"   ⚠️  Cat evaluation error: {e}")
            evaluation_results[st_name] = {
                "passed": False,
                "feedback": f"Evaluation error: {e}",
                "scores": {},
                "heuristics": {},
            }

    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("cat_evaluator", duration_ms, run_id)

    return {
        "cat_evaluation_result": evaluation_results,
        "run_id": run_id,
    }
