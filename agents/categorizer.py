"""
Categorizer Agent - Maps P&L line items to Chart of Accounts.

3-Layer Matching:
1. Token-based exact match (fast, deterministic)
2. LLM fallback for unmatched items (semantic matching with PDF definitions)
3. Human review queue for low-confidence items

Not in retry loop - runs once after extraction passes evaluation.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from utils.ollama_client import chat

from coa.chart_of_accounts import COA_ACCOUNTS, REVENUE_ACCOUNTS, DIRECT_COST_ACCOUNTS, OPERATING_EXPENSE_ACCOUNTS, serialize_coa_for_prompt
from coa.matcher import MatchConfidence, MatchResult
from config import Config
from utils.vlm_utils import StatementType


def extract_line_items_from_statement(data: dict) -> List[dict]:
    """
    Extract line items from extracted statement data.

    Args:
        data: Extracted statement data with sections and rows

    Returns:
        List of dicts with label, values, section, and original row index
    """
    line_items = []

    for section in data.get("sections", []):
        section_name = section.get("name", "")
        for idx, row in enumerate(section.get("rows", [])):
            label = row.get("label", "")
            values = row.get("values", [])

            # Skip subtotal rows and empty labels
            if row.get("is_subtotal", False) or not label:
                continue

            line_items.append({
                "label": label,
                "values": values,
                "section": section_name,
                "row_index": idx,
            })

    return line_items


MAX_BATCH_SIZE = 25


def _llm_match_single_batch(batch_items: List[dict], run_id: str, is_retry: bool, practice_id: str) -> List[dict]:
    """Call LLM for a single batch of items (worker for parallel execution)."""
    from utils.observability import get_observability
    obs = get_observability()

    # Serialize CoA for prompt context (include descriptions for semantic matching)
    coa_context = serialize_coa_for_prompt(include_descriptions=True)

    # Prepare items for batch processing
    items_json = json.dumps([
        {"label": item["label"], "section": item["section"], "values": item["values"]}
        for item in batch_items
    ], indent=2)

    # Load learned corrections for this practice
    memory_prompt = ""
    if practice_id:
        from utils.memory_manager import build_memory_prompt
        memory_prompt = build_memory_prompt(practice_id)

    retry_guidance = ""
    if is_retry:
        retry_guidance = """## RETRY MODE - Be More Aggressive

This is a re-categorization attempt. The previous run had too many unmatched or low-confidence items.
- Accept partial matches (e.g., "Staff Training" → 7780 Continuing Education)
- Use broader categories when exact match isn't available (e.g., "Office Supplies" → 7715 Computer/Office Supplies)
- If still uncertain, assign best-guess account and set confidence="medium" instead of "low"
- Avoid marking items as needs_review unless truly unclassifiable

"""

    prompt = f"""You are mapping P&L line items to Chart of Accounts (CoA) for a veterinary practice.

## IMPORTANT: Account Type Filter

Only match to **income statement accounts** (5000-9999 series):
- 5xxx: Revenue accounts
- 6xxx: Direct Cost accounts
- 7xxx: Operating Expense accounts
- 8xxx: Depreciation / Amortization
- 9xxx: Other Income, Other Expense, Interest, Taxes

NEVER match to balance sheet accounts (1000-4999):
- 1xxx: Assets (Cash, A/R, Inventory, Equipment, Vehicles)
- 2xxx: Fixed Assets and Other Assets
- 3xxx/4xxx: Liabilities and Equity

## CoA Reference

{coa_context}

## Task

Map each line item below to the best matching CoA account.

**CRITICAL: Section-Aware Matching** — The section a line item appears in determines valid account types:
- Items in a **REVENUE** section → ONLY match to 5xxx (Revenue) accounts
- Items in a **COST OF GOODS SOLD / COST OF REVENUE** section → ONLY match to 6xxx (Direct Cost) accounts
- Items in an **EXPENSES / OPERATING EXPENSES / G&A** section → ONLY match to 7xxx (Operating Expense) accounts
- Items in **OTHER INCOME / OTHER EXPENSE** sections → match to 8xxx or 9xxx accounts
- Mismatching an item's section to the wrong account series is a CRITICAL ERROR

**CRITICAL: Use the most specific account available. Never assign a parent account (e.g. 9000)
when a more specific child account (e.g. 9010, 9020, 9030) better describes the line item.**

Use these specific rules:

### Revenue Rules (5xxx)
1. **Cremation** (revenue side) → 5050 Mortuary Revenue
2. **Homeopathy** → 5070 Alternative and Complementary Medicine Revenue
3. **Food Sales / Diet Sales** → 5200 Dietary Product Revenue
4. **Drug Sales / Pharmacy** → 5100 Pharmacy Revenue

### Direct Cost Rules (6xxx)
5. **Cremation expenses** (cost side) → 6050 Animal Disposal/Mortuary Costs
6. **Laboratory fees** (when in COGS section) → 6302 Outside (Reference) Lab Costs
7. **Medical Supplies** (when in COGS section) → 6020 Examination, Hospitalization & Treatment Costs

### Operating Expense Rules (7xxx)
8. **CPP Expense** → 7200 Employer Payroll Taxes (Canada Pension Plan — Canadian payroll tax)
9. **EI Expense** → 7200 Employer Payroll Taxes (Employment Insurance — Canadian payroll tax)
10. **WHSCC Expense** → 7340 Workers Compensation Premium/Tax
11. **Management Fee** (external firm) → 7790 Business Consultation
12. **Amortization Expense** → 8050 Amortization Expense (NOT 7700)
13. **Interest on Long Term Debt / Interest Expense** → 9080 Interest Expense – Financed (NOT 7700)
14. **Income Taxes / Corporate Tax** → 9100 Federal Income Tax Provision (NOT 7700)

### Other Income / Other Expense Rules (9xxx)
15. **Rent Revenue / Apartment Revenue** → 9010 Rent Revenue (NOT 9000)
16. **Interest Revenue / Interest Income** → 9020 Interest & Dividend Revenue (NOT 9000)
17. **Dividend income / Capital Gain Dividend** → 9020 Interest & Dividend Revenue (NOT 9000)
18. **Gain on Sale of Securities** → 9030 Gain/(Loss) on Asset Disposition (NOT 9000)
19. **Gain on sale of capital assets** → 9030 Gain/(Loss) on Asset Disposition (NOT 9000)
20. **Wage Subsidy / Government subsidy programs** → 9000 Miscellaneous Revenue
    (no direct CoA match — 9000 is correct here as a genuine catch-all)

### Compound Items (flag for split)
21. **Accounting & Legal** → split_accounts field (see Output Format below)
22. **Surgery / Dentistry** → flag needs_review=true, candidates: ["5500", "5700"]
23. **Interest & Bank Charges** → flag needs_review=true, candidates: ["7905", "9080"]

{memory_prompt}

{retry_guidance}
## Line Items to Map

{items_json}

## Output Format

Return a JSON array with one object per line item:
```json
[
  {{
    "label": "original label",
    "account_id": "5050",
    "account_name": "Mortuary Revenue",
    "category": "Revenue",
    "confidence": "high|medium|low",
    "reasoning": "One sentence explaining the match",
    "needs_review": false,
    "candidates": ["5100", "5115"],
    "is_split": false,
    "split_accounts": []
  }}
]
```

For compound items that map to TWO accounts (e.g., "Accounting & Legal"):
```json
[
  {{
    "label": "Accounting & Legal",
    "account_id": "7765",
    "account_name": "Accounting Fees",
    "category": "Operating Expense",
    "confidence": "high",
    "reasoning": "Compound item split: accounting portion → 7765, legal portion → 7785",
    "needs_review": true,
    "candidates": [],
    "is_split": true,
    "split_accounts": [
      {{"account_id": "7765", "account_name": "Accounting Fees", "portion": "accounting"}},
      {{"account_id": "7785", "account_name": "Legal Services", "portion": "legal"}}
    ]
  }}
]
```

Rules for split items:
- Set `account_id` to the PRIMARY account (larger or more common portion)
- Set `is_split: true` and populate `split_accounts` with ALL accounts in the split
- Set `needs_review: true` — a human must confirm the proportional split
- Never put two account IDs in the `account_id` field (e.g. "7765/7785" is WRONG)

Confidence levels:
- **high**: Clear semantic match (e.g., Cremation → Mortuary Revenue)
- **medium**: Reasonable match but some ambiguity
- **low**: Uncertain match, needs human review

Set needs_review=true for:
- Compound items that need splitting (e.g., "Surgery / Dentistry")
- Canadian-specific items (CPP, EI, WHSCC)
- Items with no clear CoA equivalent (Wage Subsidy)
- Low confidence matches
"""

    start_time = time.time()

    response = chat(
        model=Config.EXTRACTION_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    duration_ms = (time.time() - start_time) * 1000
    obs.log_llm_call(
        model=Config.EXTRACTION_MODEL,
        duration_ms=duration_ms,
        prompt=prompt,
        response=response["message"]["content"],
        run_id=run_id
    )

    # Parse response
    content = response["message"]["content"].strip()

    # Clean markdown fences
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rstrip("`").strip()

    try:
        results = json.loads(content)
        return results
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse LLM response: {e}")
        # Return empty results on parse failure
        return [{"label": item["label"], "error": f"LLM parse error: {e}"} for item in batch_items]


def llm_match_batch(unmatched_items: List[dict], run_id: str = None, is_retry: bool = False, practice_id: str = None) -> List[dict]:
    """
    Use LLM to match unmatched line items to CoA accounts.

    Splits large item sets into batches and processes them in parallel
    via ThreadPoolExecutor to reduce total categorization time.
    Failed batches are retried sequentially to avoid overwhelming Ollama.

    Args:
        unmatched_items: List of line items that couldn't be matched by token matcher
        run_id: Optional run ID for observability
        is_retry: If True, use more aggressive matching guidance
        practice_id: Optional practice ID for loading learned corrections

    Returns:
        List of LLM match results with account_id, confidence, reasoning
    """
    if not unmatched_items:
        return []

    # No need to parallelize a single batch
    if len(unmatched_items) <= MAX_BATCH_SIZE:
        return _llm_match_single_batch(unmatched_items, run_id, is_retry, practice_id)

    # Build batches
    batches = []
    for i in range(0, len(unmatched_items), MAX_BATCH_SIZE):
        batch = unmatched_items[i:i + MAX_BATCH_SIZE]
        batches.append(batch)
        print(f"    Batching {len(batch)} items ({i+1}-{i+len(batch)} of {len(unmatched_items)})...")

    print(f"  Launching {len(batches)} batch(es) in parallel...")

    all_results = []
    failed_batches = []
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=len(batches)) as executor:
        future_to_batch = {
            executor.submit(_llm_match_single_batch, batch, run_id, is_retry, practice_id): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(future_to_batch):
            batch_idx = future_to_batch[future]
            try:
                batch_results = future.result()
                all_results.extend(batch_results)
                print(f"    Batch {batch_idx + 1}/{len(batches)} complete ({len(batch_results)} results)")
            except Exception as e:
                logging.error(f"Batch {batch_idx + 1} failed: {e}")
                print(f"    ⚠️  Batch {batch_idx + 1}/{len(batches)} failed: {e}")
                failed_batches.append((batch_idx, batches[batch_idx]))

    # Retry failed batches sequentially with backoff
    if failed_batches:
        print(f"  Retrying {len(failed_batches)} failed batch(s) sequentially...")
        for attempt, (batch_idx, batch) in enumerate(failed_batches, 1):
            if attempt > 1:
                time.sleep(5)  # Brief cooldown between sequential retries
            try:
                batch_results = _llm_match_single_batch(batch, run_id, is_retry, practice_id)
                all_results.extend(batch_results)
                print(f"    Retry batch {batch_idx + 1} complete ({len(batch_results)} results)")
            except Exception as e:
                logging.error(f"Retry batch {batch_idx + 1} failed again: {e}")
                print(f"    ❌ Retry batch {batch_idx + 1} failed again: {e}")

    return all_results


def apply_categorization_to_statement(
    data: dict,
    match_results: Dict[str, MatchResult],
    llm_results: List[dict]
) -> dict:
    """
    Apply categorization results to extracted statement data.

    Handles:
    - Section headers: marked with line_type="header", no account ID
    - Postable line items: assigned CoA account with confidence/metadata
    - Subtotals: preserved as-is

    Args:
        data: Original extracted statement data
        match_results: Token matcher results by label
        llm_results: LLM fallback results

    Returns:
        Updated statement data with categorization metadata
    """
    from coa.matcher import is_section_header

    # Build lookup for LLM results
    llm_lookup = {r["label"]: r for r in llm_results if "account_id" in r}

    # Categorize each section and row
    categorized_sections = []

    for section in data.get("sections", []):
        section_name = section.get("name", "")
        categorized_rows = []

        for row in section.get("rows", []):
            label = row.get("label", "")
            if not label or row.get("is_subtotal", False):
                categorized_rows.append(row)
                continue

            # Check if this is a section header
            if is_section_header(label):
                # Section header - no account assigned, just mark it
                categorized_row = dict(row)
                categorized_row["line_type"] = "section_header"
                categorized_row["categorization"] = {
                    "coa_code": None,
                    "coa_name": None,
                    "coa_category": None,
                    "match_type": "section_header",
                    "confidence": "high",
                    "reasoning": "Structural label for grouping line items, not a postable account",
                    "needs_review": False,
                    "citation": "N/A - section header",
                    "is_split": False,
                    "split_accounts": [],
                }
                categorized_rows.append(categorized_row)
                continue

            # Find categorization result for postable line item
            result = match_results.get(label)
            llm_result = llm_lookup.get(label)

            categorization = None

            if result and result.account:
                # Auto-generate reasoning for token/exact matches
                match_type_val = result.match_type.value if result.match_type else "token"
                if match_type_val == "exact":
                    reasoning = f"Exact name match → {result.account.code} {result.account.name}"
                elif match_type_val == "exact_substring":
                    reasoning = f"Substring match ('{result.matched_on[0]}') → {result.account.code} {result.account.name}"
                else:
                    matched_str = ", ".join(result.matched_on) if result.matched_on else "unknown token"
                    reasoning = f"Token match on '{matched_str}' → {result.account.code} {result.account.name}"

                needs_review = result.confidence.value != "high"
                categorization = {
                    "coa_code": result.account.code,
                    "coa_name": result.account.name,
                    "coa_category": result.account.category,
                    "match_type": match_type_val,
                    "confidence": result.confidence.value,
                    "matched_on": result.matched_on,
                    "reasoning": reasoning,
                    "needs_review": needs_review,
                    "citation": f"VMG/AAHA CoA p.{result.account.code[:2]}00",
                    "is_split": False,
                    "split_accounts": [],
                }
            elif llm_result and "account_id" in llm_result:
                conf = llm_result.get("confidence", "low")
                is_split = llm_result.get("is_split", False)
                split_accounts = llm_result.get("split_accounts", [])

                categorization = {
                    "coa_code": llm_result["account_id"],
                    "coa_name": llm_result.get("account_name", ""),
                    "coa_category": llm_result.get("category", ""),
                    "match_type": "llm_split" if is_split else "llm",
                    "confidence": conf,
                    "reasoning": llm_result.get("reasoning", ""),
                    "needs_review": conf == "low" or llm_result.get("needs_review", False),
                    "citation": "VMG/AAHA DATALINK Definitions",
                    # New split fields
                    "is_split": is_split,
                    "split_accounts": split_accounts,
                }

            # Add categorization to row
            categorized_row = dict(row)
            if categorization:
                categorized_row["categorization"] = categorization
            else:
                categorized_row["categorization"] = {
                    "coa_code": None,
                    "coa_name": None,
                    "coa_category": None,
                    "match_type": "unmatched",
                    "confidence": "unmatched",
                    "reasoning": "No matching CoA account found — requires manual review",
                    "needs_review": True,
                    "citation": "N/A - needs human review",
                    "is_split": False,
                    "split_accounts": [],
                }
            categorized_rows.append(categorized_row)

        categorized_sections.append({
            "name": section_name,
            "rows": categorized_rows,
        })

    return {
        **data,
        "sections": categorized_sections,
    }


def _merge_selective_categorization(
    prev_categorized_data: dict,
    new_llm_results: List[dict]
) -> dict:
    """
    Merge new LLM results into previous categorized data, preserving good results.

    On selective retry, only rows with updated LLM results are modified.
    All other rows keep their previous categorization unchanged.

    Args:
        prev_categorized_data: Full categorized data from previous pass
        new_llm_results: LLM results for only the retry subset

    Returns:
        Updated categorized data with merged results
    """
    new_llm_lookup = {r["label"]: r for r in new_llm_results if "account_id" in r}

    merged_sections = []
    for section in prev_categorized_data.get("sections", []):
        section_name = section.get("name", "")
        merged_rows = []

        for row in section.get("rows", []):
            label = row.get("label", "")
            if not label or row.get("is_subtotal", False):
                merged_rows.append(row)
                continue

            cat = row.get("categorization", {})
            # Only update rows that were in the retry set (new LLM result exists)
            if label in new_llm_lookup and cat.get("needs_review"):
                llm_result = new_llm_lookup[label]
                conf = llm_result.get("confidence", "low")
                is_split = llm_result.get("is_split", False)
                split_accounts = llm_result.get("split_accounts", [])

                updated_row = dict(row)
                updated_row["categorization"] = {
                    "coa_code": llm_result["account_id"],
                    "coa_name": llm_result.get("account_name", ""),
                    "coa_category": llm_result.get("category", ""),
                    "match_type": "llm_split" if is_split else "llm_retry",
                    "confidence": conf,
                    "reasoning": llm_result.get("reasoning", ""),
                    "needs_review": conf == "low" or llm_result.get("needs_review", False),
                    "citation": "VMG/AAHA DATALINK Definitions (retry)",
                    "is_split": is_split,
                    "split_accounts": split_accounts,
                }
                merged_rows.append(updated_row)
            else:
                # Preserve previous categorization unchanged
                merged_rows.append(row)

        merged_sections.append({
            "name": section_name,
            "rows": merged_rows,
        })

    return {
        **prev_categorized_data,
        "sections": merged_sections,
    }


def categorizer_node(state: dict) -> dict:
    """
    Categorize extracted financial statement line items to CoA.

    Args:
        state: Current workflow state with extracted_data,
               optional cat_retry_count for retry behavior

    Returns:
        Updated state with categorized_data, categorization_summary, review_queue
    """
    from utils.observability import get_observability

    from pathlib import Path
    obs = get_observability()
    run_id = state.get("run_id")
    start_time = time.time()
    cat_retry_count = state.get("cat_retry_count", 0)
    pdf_path = state.get("input_pdf", "")
    practice_id = Path(pdf_path).stem if pdf_path else None

    extracted_data = state.get("extracted_data", {})
    if not extracted_data:
        logging.warning("No extracted data to categorize")
        return {
            "categorized_data": {},
            "categorization_summary": {},
            "review_queue": [],
            "cat_retry_count": cat_retry_count,
            "run_id": run_id
        }

    categorized_data = {}
    all_review_items = []
    summary_stats = {
        "total_line_items": 0,
        "auto_categorized": 0,
        "llm_matched": 0,
        "needs_review": 0,
    }

    # Process each statement type
    for statement_type, data in extracted_data.items():
        statement_name = statement_type.value if hasattr(statement_type, 'value') else str(statement_type)

        # Only categorize income statement for now (P&L has line items that map to CoA)
        if statement_name != "income_statement":
            categorized_data[statement_type] = data
            continue

        is_retry = cat_retry_count > 0

        logging.info(f"Categorizing {statement_name}...")
        if is_retry:
            print(f"\n🏷️  Categorizing {statement_name.replace('_', ' ').title()} (retry {cat_retry_count})...")
        else:
            print(f"\n🏷️  Categorizing {statement_name.replace('_', ' ').title()}...")

        # Extract line items and separate section headers from postable items
        line_items = extract_line_items_from_statement(data)
        from coa.matcher import is_section_header

        section_headers = []
        postable_items = []
        for item in line_items:
            if is_section_header(item["label"]):
                section_headers.append(item)
                print(f"    – {item['label']} → [section header, no account]")
            else:
                postable_items.append(item)

        summary_stats["total_line_items"] += len(line_items)

        if is_retry and state.get("categorized_data"):
            # Selective retry: only re-categorize items that needed review previously
            previous_categorized = state.get("categorized_data", {})
            prev_data = previous_categorized.get(statement_type)

            if prev_data:
                retry_labels = set()
                for section in prev_data.get("sections", []):
                    for row in section.get("rows", []):
                        cat = row.get("categorization", {})
                        if cat and cat.get("needs_review"):
                            retry_labels.add(row.get("label"))

                if retry_labels:
                    retry_items = [item for item in postable_items if item["label"] in retry_labels]
                    print(f"  Selective retry: {len(retry_items)} of {len(postable_items)} items need re-categorization...")
                    llm_results = llm_match_batch(retry_items, run_id, is_retry=is_retry, practice_id=practice_id)

                    for llm_result in llm_results:
                        if "account_id" in llm_result:
                            summary_stats["llm_matched"] += 1
                            if llm_result.get("confidence") == "low" or llm_result.get("needs_review"):
                                summary_stats["needs_review"] += 1
                                all_review_items.append({
                                    "label": llm_result["label"],
                                    "section": next((item["section"] for item in retry_items if item["label"] == llm_result["label"]), ""),
                                    "values": llm_result.get("values", []),
                                    "account_id": llm_result["account_id"],
                                    "account_name": llm_result.get("account_name", ""),
                                    "confidence": llm_result.get("confidence", "low"),
                                    "reasoning": llm_result.get("reasoning", ""),
                                })

                    categorized = _merge_selective_categorization(prev_data, llm_results)
                else:
                    print("  No items need retry — keeping previous categorization")
                    categorized = prev_data
            else:
                # Fallback: no previous data, categorize all
                llm_results = llm_match_batch(postable_items, run_id, is_retry=is_retry, practice_id=practice_id)

                for llm_result in llm_results:
                    if "account_id" in llm_result:
                        summary_stats["llm_matched"] += 1
                        if llm_result.get("confidence") == "low" or llm_result.get("needs_review"):
                            summary_stats["needs_review"] += 1
                            all_review_items.append({
                                "label": llm_result["label"],
                                "section": next((item["section"] for item in postable_items if item["label"] == llm_result["label"]), ""),
                                "values": llm_result.get("values", []),
                                "account_id": llm_result["account_id"],
                                "account_name": llm_result.get("account_name", ""),
                                "confidence": llm_result.get("confidence", "low"),
                                "reasoning": llm_result.get("reasoning", ""),
                            })

                match_results: Dict[str, MatchResult] = {}
                categorized = apply_categorization_to_statement(data, match_results, llm_results)
        else:
            # First pass: categorize all items
            print(f"  LLM matching for {len(postable_items)} items with section-aware rules...")
            llm_results = llm_match_batch(postable_items, run_id, is_retry=is_retry, practice_id=practice_id)

            for llm_result in llm_results:
                if "account_id" in llm_result:
                    summary_stats["llm_matched"] += 1
                    if llm_result.get("confidence") == "low" or llm_result.get("needs_review"):
                        summary_stats["needs_review"] += 1
                        all_review_items.append({
                            "label": llm_result["label"],
                            "section": next((item["section"] for item in postable_items if item["label"] == llm_result["label"]), ""),
                            "values": llm_result.get("values", []),
                            "account_id": llm_result["account_id"],
                            "account_name": llm_result.get("account_name", ""),
                            "confidence": llm_result.get("confidence", "low"),
                            "reasoning": llm_result.get("reasoning", ""),
                        })

            # Apply categorization with empty token results (all LLM-based)
            match_results: Dict[str, MatchResult] = {}
            categorized = apply_categorization_to_statement(data, match_results, llm_results)

        categorized_data[statement_type] = categorized

        # Print summary
        print(f"  Summary: {summary_stats['llm_matched']} categorized, {summary_stats['needs_review']} need review")

    # Increment cat retry count
    new_cat_retry_count = cat_retry_count + 1

    # Log node timing
    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("categorizer", duration_ms, run_id)

    return {
        "categorized_data": categorized_data,
        "categorization_summary": summary_stats,
        "review_queue": all_review_items,
        "cat_retry_count": new_cat_retry_count,
        "run_id": run_id
    }
