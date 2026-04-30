"""
Chart of Accounts (CoA) module for VMG/AAHA veterinary accounting.

Provides:
- COAAccount dataclass with code, name, category, description, aliases
- COA_ACCOUNTS: Dict of all accounts indexed by code
- COA_NAME_INDEX: Dict for O(1) lookup by name/alias
- Matcher functions for P&L line item categorization
"""

from coa.chart_of_accounts import (
    COAAccount,
    COA_ACCOUNTS,
    COA_NAME_INDEX,
    REVENUE_ACCOUNTS,
    DIRECT_COST_ACCOUNTS,
    OPERATING_EXPENSE_ACCOUNTS,
    OTHER_EXPENSE_ACCOUNTS,
    OTHER_INCOME_EXPENSE_ACCOUNTS,
    get_account_by_code,
    get_accounts_by_series,
    serialize_coa_for_prompt,
)
from coa.matcher import (
    MatchResult,
    MatchConfidence,
    MatchType,
    match_line_item,
    match_all_line_items,
)

__all__ = [
    "COAAccount",
    "COA_ACCOUNTS",
    "COA_NAME_INDEX",
    "REVENUE_ACCOUNTS",
    "DIRECT_COST_ACCOUNTS",
    "OPERATING_EXPENSE_ACCOUNTS",
    "OTHER_EXPENSE_ACCOUNTS",
    "OTHER_INCOME_EXPENSE_ACCOUNTS",
    "get_account_by_code",
    "get_accounts_by_series",
    "serialize_coa_for_prompt",
    "MatchResult",
    "MatchConfidence",
    "MatchType",
    "match_line_item",
    "match_all_line_items",
]
