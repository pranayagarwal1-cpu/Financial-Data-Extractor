"""
Token-based matcher for P&L line items to Chart of Accounts.

3-Layer Matching Approach:
1. Exact match (case-insensitive, substring match)
2. Token-based match (strip filler words, compare core tokens with stemming)
3. No match → defer to LLM fallback

Match Confidence Levels:
- HIGH: Single exact or token match
- MEDIUM: Partial token match (some tokens unmatched)
- AMBIGUOUS: Multiple candidate matches
- UNMATCHED: No match found (defer to LLM)
- COMPOUND: Line item contains multiple categories (e.g., "Surgery / Dentistry")
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

from coa.chart_of_accounts import (
    COAAccount,
    COA_ACCOUNTS,
    COA_NAME_INDEX,
    REVENUE_ACCOUNTS,
    DIRECT_COST_ACCOUNTS,
    OPERATING_EXPENSE_ACCOUNTS,
)


class MatchConfidence(Enum):
    """Confidence levels for matching."""
    HIGH = "high"           # Single exact match - auto-approve
    MEDIUM = "medium"       # Token match with some unmatched tokens
    AMBIGUOUS = "ambiguous" # Multiple matches - human review
    UNMATCHED = "unmatched" # No match - LLM fallback
    COMPOUND = "compound"   # Multiple categories in one label - human review


class MatchType(Enum):
    """Type of match found."""
    EXACT = "exact"                 # Exact string match
    EXACT_SUBSTRING = "exact_substring"  # P&L label is substring of CoA name
    TOKEN = "token"                 # Token-based match with stemming
    TOKEN_PARTIAL = "token_partial" # Partial token match
    LLM = "llm"                     # Matched by LLM fallback


@dataclass
class MatchResult:
    """
    Result of matching a P&L line item to a CoA account.

    Attributes:
        line_item: Original P&L line item label
        account: Matched CoA account (None if unmatched)
        confidence: Match confidence level
        match_type: Type of match (exact, token, llm, etc.)
        matched_on: Token(s) that matched
        pnl_unmatched: Tokens from P&L that didn't match
        candidates: Alternative candidate accounts (for ambiguous/compound)
        reasoning: Explanation of match decision (populated by LLM)
    """
    line_item: str
    account: Optional[COAAccount] = None
    confidence: MatchConfidence = MatchConfidence.UNMATCHED
    match_type: Optional[MatchType] = None
    matched_on: List[str] = field(default_factory=list)
    pnl_unmatched: List[str] = field(default_factory=list)
    candidates: List[Dict] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "line_item": self.line_item,
            "account_id": self.account.code if self.account else None,
            "account_name": self.account.name if self.account else None,
            "category": self.account.category if self.account else None,
            "confidence": self.confidence.value,
            "match_type": self.match_type.value if self.match_type else None,
            "matched_on": self.matched_on,
            "pnl_unmatched": self.pnl_unmatched,
            "candidates": self.candidates,
            "reasoning": self.reasoning,
        }


# Filler words to strip during tokenization
FILLER_WORDS = {
    "revenue", "expense", "expenses", "cost", "costs",
    "sales", "service", "services", "fees", "total",
    "other", "general", "administrative", "the", "and",
    "of", "for", "from", "with", "on", "in", "to",
    "program", "programmes", "payments", "adjustments",
    "discounts", "returns", "allowances", "write-off",
    "adjustment", "subsidy", "expansion", "salary", "rebate"
}

# Compound separators - indicate multiple categories
COMPOUND_SEPARATORS = ["/", " & ", " and ", " + "]

# Section header patterns - structural labels, not postable line items
# These must be specific enough to NOT catch items like "EI Expense", "Interest Revenue", etc.
SECTION_HEADER_PATTERNS = [
    r"^total\s+(revenue|expenses?|income|cost|sales)$",     # "Total Revenue", "Total Expenses"
    r"^(revenue|expenses?|income|cost|sales)\s+total$",     # "Revenue Total"
    r"^other\s+(revenue|expenses?|income)$",                 # "Other Revenue", "Other Expenses"
    r"^(gross|net)\s+(profit|income|loss|revenue|margin)$", # "Gross Profit", "Net Income"
    r"^operating\s+(expenses?|income|profit|revenue)$",     # "Operating Expenses"
    r"^cost\s+of\s+(goods\s+sold|revenue|sales)$",          # "Cost of Goods Sold"
    r"^(revenue|expenses?|income)$",                         # Just "Revenue", "Expenses", "Income"
    r"^(veterinary|practice)\s+(revenue|income|sales)$",    # "Veterinary Revenue"
]

# Income statement accounts only (exclude balance sheet: 1000-3999, 4000 equity)
# P&L line items should only match to: 5xxx (Revenue), 6xxx (Direct Cost), 7xxx (OpEx), 8xxx/9xxx (Other)
INCOME_STATEMENT_ACCOUNTS = {
    code: acc for code, acc in COA_ACCOUNTS.items()
    if code.startswith(("5", "6", "7", "8", "9"))
}

# Stemming rules (simplified Porter stemmer for accounting terms)
# Order matters - more specific patterns first
STEM_RULES = [
    (r"ations?$", "ate"),        # vaccinations -> vaccin ate -> we want "vaccin"
    (r"ication$", "icate"),      # application -> applic
    (r"ings?$", ""),             # boarding -> board (remove -ing entirely)
    (r"ies?$", "y"),             # diagnostics -> diagnost
    (r"es?$", ""),               # vaccines -> vaccin (remove -e/-s)
    (r"s$", ""),                 # exams -> exam
    (r"ment$", ""),              # payment -> pay
    (r"able$", ""),              # payable -> pay
    (r"ible$", ""),              # visible -> vis
    (r"tion$", ""),              # consultation -> consulta
    (r"sis$", "s"),              # analysis -> analys
    (r"al$", ""),                # referral -> refer
    (r"ic$", ""),                # diagnostic -> diagnost
]

# Manual stem mappings for common accounting terms
MANUAL_STEMS = {
    "vaccinations": "vaccin",
    "vaccine": "vaccin",
    "vaccin": "vaccin",
    "diagnostics": "diagnost",
    "diagnostic": "diagnost",
    "diagnose": "diagnost",
    "boarding": "board",
    "board": "board",
    "cremation": "cremat",
    "cremations": "cremat",
    "mortuary": "mortuar",
    "homeopathy": "homeopath",
    "homeopathic": "homeopath",
    "alternative": "altern",
    "complementary": "complement",
    "medicine": "medicin",
    "medicines": "medicin",
    "medical": "medic",
    "surgery": "surg",
    "surgical": "surg",
    "dentistry": "dent",
    "dental": "dent",
    "exams": "exam",
    "exam": "exam",
    "examination": "exam",
    "consultations": "consult",
    "consultation": "consult",
    "consult": "consult",
    "food": "food",
    "foods": "food",
    "dietary": "diet",
    "diet": "diet",
    "diets": "diet",
    "drug": "drug",
    "drugs": "drug",
    "pharmacy": "pharm",
    "pharmaceutical": "pharm",
    "pharmacist": "pharm",
    "sales": "sale",
    "sale": "sale",
    "revenue": "revenu",
    "revenues": "revenu",
    "expense": "expens",
    "expenses": "expens",
    "cost": "cost",
    "costs": "cost",
    "cpp": "cpp",
    "ei": "ei",
    "whscc": "whscc",
    "payroll": "payroll",
    "tax": "tax",
    "taxes": "tax",
    "accounting": "account",
    "account": "account",
    "legal": "leg",
    "law": "law",
    "attorney": "attorney",
}


def stem(word: str) -> str:
    """
    Apply simplified stemming to a word.

    Uses manual stem mappings first, then falls back to rule-based stemming.

    Examples:
        "vaccinations" -> "vaccin"
        "boarding" -> "board"
        "exams" -> "exam"
    """
    word_lower = word.lower()

    # Check manual stems first (most accurate)
    if word_lower in MANUAL_STEMS:
        return MANUAL_STEMS[word_lower]

    # Fall back to rule-based stemming
    for pattern, replacement in STEM_RULES:
        if re.search(pattern, word_lower):
            return re.sub(pattern, replacement, word_lower)

    return word_lower


def tokenize(text: str, strip_filler: bool = True) -> Set[str]:
    """
    Tokenize a string into core meaning words.

    Args:
        text: Input string to tokenize
        strip_filler: Whether to remove filler words

    Returns:
        Set of stemmed tokens

    Examples:
        "Vaccinations Revenue" -> {"vaccin"}
        "Exams / Consultations" -> {"exam", "consult"}
        "Surgery / Dentistry" -> {"surg", "dent"}
    """
    # Convert to lowercase
    text = text.lower()

    # Split on compound separators first
    for sep in COMPOUND_SEPARATORS:
        if sep in text:
            # For compound detection, we keep the parts separate
            pass

    # Remove punctuation except hyphens within words
    text = re.sub(r"[^\w\s-]", " ", text)

    # Split into tokens
    tokens = set(text.split())

    # Strip filler words
    if strip_filler:
        tokens = tokens - FILLER_WORDS

    # Filter very short tokens
    tokens = {t for t in tokens if len(t) > 2}

    # Stem tokens
    tokens = {stem(t) for t in tokens}

    return tokens


def is_compound_label(label: str) -> bool:
    """
    Check if a label contains multiple categories.

    Examples:
        "Surgery / Dentistry" -> True
        "Exams / Consultations" -> True (but same category)
        "Vaccinations" -> False
    """
    for sep in COMPOUND_SEPARATORS:
        if sep in label:
            return True
    return False


def is_section_header(label: str) -> bool:
    """
    Check if a label is a structural section header rather than a postable line item.

    Section headers are grouping labels like "Veterinary Practice Revenue" or
    "General & Administrative Expenses" that organize line items but don't
    represent accounts themselves.

    Signals:
    - Ends with "Revenue", "Expense", "Expenses", "Income", "Costs"
    - Starts with "Total", "Other"
    - Is just "Revenue", "Expenses", etc.
    - No numeric value associated (caller should check this)

    Returns:
        True if this appears to be a section header
    """
    label_lower = label.lower().strip()

    # Check regex patterns
    for pattern in SECTION_HEADER_PATTERNS:
        if re.search(pattern, label_lower):
            return True

    # Additional heuristic: very short labels that are just category names
    if label_lower in {"revenue", "expenses", "income", "costs", "other income", "other expenses"}:
        return True

    return False


def find_overlap(pnl_tokens: Set[str], coa_tokens: Set[str]) -> Tuple[Set[str], Set[str]]:
    """
    Find overlapping tokens between P&L label and CoA account.

    Uses exact stem match only - no substring matching to avoid false positives
    like "re" (from Real Estate) matching "cremat" (from Cremation).

    Returns:
        Tuple of (matched_tokens, pnl_unmatched_tokens)
    """
    matched = set()
    pnl_remaining = set(pnl_tokens)

    for pnl_token in pnl_tokens:
        for coa_token in coa_tokens:
            # Exact stem match only
            if pnl_token == coa_token:
                matched.add(pnl_token)
                pnl_remaining.discard(pnl_token)
                break
            # Word boundary match: one token starts with the other (min 5 chars)
            # This catches "vaccin" == "vaccin" but not "re" in "cremat"
            elif len(pnl_token) >= 5 and len(coa_token) >= 5:
                if pnl_token.startswith(coa_token) or coa_token.startswith(pnl_token):
                    matched.add(pnl_token)
                    pnl_remaining.discard(pnl_token)
                    break

    return matched, pnl_remaining


def get_allowed_series(section_name: str) -> set:
    """
    Determine which account series are valid for a given P&L section.

    Args:
        section_name: Section name from the P&L (e.g., 'REVENUE', 'COST OF GOODS SOLD')

    Returns:
        Set of allowed first-digit prefixes (e.g., {'5'}, {'6'}, {'7'}, or all)
    """
    s = section_name.upper().strip()

    revenue_keywords = ["REVENUE", "INCOME", "SALES", "FEES EARNED", "TURNOVER"]
    cogs_keywords = ["COST OF", "COGS", "COST OF SALES", "COST OF REVENUE",
                     "DIRECT COST", "COST OF GOODS"]
    opex_keywords = ["EXPENSE", "EXPENSES", "OPERATING", "G&A", "GENERAL",
                     "ADMINISTRATIVE", "SELLING", "DISTRIBUTION",
                     "OVERHEAD", "PAYROLL"]

    for kw in revenue_keywords:
        if kw in s:
            return {"5"}
    for kw in cogs_keywords:
        if kw in s:
            return {"6"}
    for kw in opex_keywords:
        if kw in s:
            return {"7"}

    # Default: allow all P&L accounts
    return {"5", "6", "7", "8", "9"}


def filter_accounts_by_section(
    accounts: Optional[Dict[str, COAAccount]] = None,
    section_name: str = ""
) -> Dict[str, COAAccount]:
    """
    Filter accounts to only those valid for a given section.

    Args:
        accounts: Account dict to filter (defaults to income statement accounts)
        section_name: Section name for filtering

    Returns:
        Filtered account dict
    """
    if accounts is None:
        accounts = INCOME_STATEMENT_ACCOUNTS

    if not section_name:
        return accounts

    allowed = get_allowed_series(section_name)

    if allowed == {"5", "6", "7", "8", "9"}:
        return accounts

    return {
        code: acc for code, acc in accounts.items()
        if code[0] in allowed
    }


def match_line_item(
    line_item: str,
    accounts: Optional[Dict[str, COAAccount]] = None,
    allow_balance_sheet: bool = False
) -> MatchResult:
    """
    Match a P&L line item to a CoA account using 3-layer approach.

    Layer 0: Section header detection (skip matching for structural labels)
    Layer 1: Exact match (case-insensitive, substring)
    Layer 2: Token-based match (strip filler, compare core tokens)
    Layer 3: No match → mark for LLM fallback

    Args:
        line_item: P&L line item label
        accounts: Optional dict of accounts to search (default: income statement accounts only)
        allow_balance_sheet: If True, include balance sheet accounts (1000-3999) in search

    Returns:
        MatchResult with confidence and match details
    """
    # Default to income statement accounts only (exclude balance sheet)
    if accounts is None:
        accounts = INCOME_STATEMENT_ACCOUNTS if not allow_balance_sheet else COA_ACCOUNTS

    result = MatchResult(line_item=line_item)

    # === LAYER 0: Section header detection ===
    # Structural labels like "Veterinary Practice Revenue" are not postable line items
    if is_section_header(line_item):
        result.confidence = MatchConfidence.HIGH
        result.match_type = None  # No account assigned
        result.reasoning = "Section header - structural label, not a postable line item"
        return result

    result = MatchResult(line_item=line_item)

    # Check for compound label
    if is_compound_label(line_item):
        result.confidence = MatchConfidence.COMPOUND
        result.matched_on = ["compound_separator"]
        # Split and find candidates for each part
        parts = re.split(r"\s*[/&]\s*|\s+and\s+", line_item)
        for part in parts:
            part = part.strip()
            if part:
                sub_result = match_line_item(part, accounts)
                if sub_result.account:
                    result.candidates.append({
                        "code": sub_result.account.code,
                        "name": sub_result.account.name,
                        "matched_part": part
                    })
        return result

    # === LAYER 1: Exact match ===
    line_lower = line_item.lower().strip()

    for code, account in accounts.items():
        acc_name_lower = account.name.lower()

        # Exact match
        if line_lower == acc_name_lower:
            result.account = account
            result.confidence = MatchConfidence.HIGH
            result.match_type = MatchType.EXACT
            result.matched_on = [line_item]
            return result

        # Substring match (P&L label is substring of CoA name)
        if line_lower in acc_name_lower and len(line_lower) > 4:
            result.account = account
            result.confidence = MatchConfidence.HIGH
            result.match_type = MatchType.EXACT_SUBSTRING
            result.matched_on = [line_item]
            return result

        # Reverse substring (CoA name is substring of P&L label)
        if acc_name_lower in line_lower and len(acc_name_lower) > 4:
            result.account = account
            result.confidence = MatchConfidence.MEDIUM
            result.match_type = MatchType.EXACT_SUBSTRING
            result.matched_on = [account.name]
            result.pnl_unmatched = list(tokenize(line_item) - tokenize(account.name))
            return result

    # === LAYER 2: Token-based match ===
    pnl_tokens = tokenize(line_item)

    candidates = []
    for code, account in accounts.items():
        coa_tokens = tokenize(account.name)

        if not coa_tokens:
            continue

        matched, unmatched = find_overlap(pnl_tokens, coa_tokens)

        if matched:
            candidates.append({
                "account": account,
                "matched": matched,
                "unmatched": unmatched,
                "match_ratio": len(matched) / len(pnl_tokens) if pnl_tokens else 0
            })

    if not candidates:
        # === LAYER 3: No match → LLM fallback ===
        result.confidence = MatchConfidence.UNMATCHED
        result.match_type = None
        result.pnl_unmatched = list(pnl_tokens)
        return result

    # Sort candidates by match ratio
    candidates.sort(key=lambda c: c["match_ratio"], reverse=True)
    best = candidates[0]

    # Determine confidence based on match quality
    if best["match_ratio"] == 1.0 and len(candidates) == 1:
        # All P&L tokens matched, single candidate
        result.account = best["account"]
        result.confidence = MatchConfidence.HIGH
        result.match_type = MatchType.TOKEN
        result.matched_on = list(best["matched"])
        result.pnl_unmatched = list(best["unmatched"])

    elif best["match_ratio"] == 1.0:
        # All tokens matched, but multiple candidates
        result.account = best["account"]
        result.confidence = MatchConfidence.AMBIGUOUS
        result.match_type = MatchType.TOKEN
        result.matched_on = list(best["matched"])
        result.pnl_unmatched = list(best["unmatched"])
        result.candidates = [
            {"code": c["account"].code, "name": c["account"].name}
            for c in candidates[1:3]  # Top 2 alternatives
        ]

    else:
        # Partial match
        result.account = best["account"]
        result.confidence = MatchConfidence.MEDIUM
        result.match_type = MatchType.TOKEN_PARTIAL
        result.matched_on = list(best["matched"])
        result.pnl_unmatched = list(best["unmatched"])

    return result


def match_all_line_items(
    line_items: List[str],
    accounts: Optional[Dict[str, COAAccount]] = None
) -> List[MatchResult]:
    """
    Match multiple P&L line items to CoA accounts.

    Args:
        line_items: List of P&L line item labels
        accounts: Optional dict of accounts to search

    Returns:
        List of MatchResult objects
    """
    results = []
    for item in line_items:
        results.append(match_line_item(item, accounts))
    return results


def get_match_summary(results: List[MatchResult]) -> dict:
    """
    Generate summary statistics for match results.

    Returns:
        Dict with match statistics
    """
    total = len(results)
    by_confidence = {}
    by_match_type = {}
    needs_review = []
    auto_categorized = []

    for r in results:
        conf = r.confidence.value
        by_confidence[conf] = by_confidence.get(conf, 0) + 1

        if r.match_type:
            mt = r.match_type.value
            by_match_type[mt] = by_match_type.get(mt, 0) + 1

        if r.confidence in (MatchConfidence.HIGH,):
            auto_categorized.append(r)
        else:
            needs_review.append(r)

    return {
        "total_line_items": total,
        "auto_categorized": len(auto_categorized),
        "needs_review": len(needs_review),
        "match_rate": len(auto_categorized) / total if total > 0 else 0,
        "by_confidence": by_confidence,
        "by_match_type": by_match_type,
    }
