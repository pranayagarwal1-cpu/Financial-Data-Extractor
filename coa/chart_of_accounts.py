"""
Chart of Accounts reference data for VMG/AAHA veterinary accounting.

Loaded from coa_data.json which is generated from:
- VMGAAHA_COA_Excel_Version_2025.xlsx (account codes and names)
- 2025-DATALINK-Entry-Revenue-and-Expense-Field-Definitions-10-1-25.pdf (descriptions)
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path

# Base directory for coa module
COA_DIR = Path(__file__).parent


@dataclass
class COAAccount:
    """
    A single Chart of Accounts entry.

    Attributes:
        code: 4-digit account code (e.g., "5050")
        name: Account name (e.g., "Mortuary Revenue")
        category: High-level category (Revenue, Direct Cost, Labor, G&A, Other)
        series: Account series (e.g., "5000" for Professional Services Revenue)
        description: Full description from PDF field definitions
        aliases: Alternative names for matching (populated at runtime)
    """
    code: str
    name: str
    category: str = ""
    series: str = ""
    description: str = ""
    aliases: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Auto-populate series from code
        if not self.series and self.code:
            self.series = self.code[:2] + "00"

        # Auto-populate category from series
        if not self.category:
            self.category = get_category_for_series(self.series)

        # Auto-populate aliases from name
        if not self.aliases:
            self.aliases = generate_aliases(self.name)


def get_category_for_series(series: str) -> str:
    """Map account series to high-level category."""
    series_map = {
        "1000": "Asset",
        "2000": "Asset",
        "3000": "Liability",
        "4000": "Equity",
        "5000": "Revenue",
        "6000": "Direct Cost",
        "7000": "Operating Expense",
        "8000": "Other Expense",
        "9000": "Other",
    }
    return series_map.get(series, "Unknown")


def generate_aliases(name: str) -> List[str]:
    """
    Generate alias variations for an account name.

    Examples:
        "Boarding Revenue" -> ["Boarding", "Boarding Revenue"]
        "Vaccine Revenue" -> ["Vaccine", "Vaccine Revenue"]
        "Anesthesia, Sedatives, Tranquilizers Revenue" -> ["Anesthesia", "Sedatives", "Tranquilizers"]
    """
    aliases = [name]  # Always include full name

    # Split on common separators
    parts = name.replace(",", "").replace("&", "").replace("/", " ").split()

    # Extract meaningful single-word aliases
    filler_words = {
        "revenue", "expense", "expenses", "cost", "costs",
        "services", "service", "sales", "fees", "total",
        "other", "and", "the", "a", "an"
    }

    # Add individual meaningful words
    for part in parts:
        if part.lower() not in filler_words and len(part) > 2:
            aliases.append(part)

    # Add compound aliases (e.g., "Flea Tick" from "Flea/Tick")
    if "/" in name:
        for part in name.split("/"):
            part = part.strip()
            if part and part.lower() not in filler_words:
                aliases.append(part)

    return list(set(aliases))


# Global account registries
COA_ACCOUNTS: Dict[str, COAAccount] = {}
COA_NAME_INDEX: Dict[str, str] = {}  # name/alias -> code mapping
REVENUE_ACCOUNTS: Dict[str, COAAccount] = {}
DIRECT_COST_ACCOUNTS: Dict[str, COAAccount] = {}
OPERATING_EXPENSE_ACCOUNTS: Dict[str, COAAccount] = {}
OTHER_EXPENSE_ACCOUNTS: Dict[str, COAAccount] = {}        # 8xxx series
OTHER_INCOME_EXPENSE_ACCOUNTS: Dict[str, COAAccount] = {}  # 9xxx series


def _load_accounts():
    """Load accounts from JSON data file."""
    global COA_ACCOUNTS, COA_NAME_INDEX, REVENUE_ACCOUNTS, DIRECT_COST_ACCOUNTS, OPERATING_EXPENSE_ACCOUNTS, OTHER_EXPENSE_ACCOUNTS, OTHER_INCOME_EXPENSE_ACCOUNTS

    data_file = COA_DIR / "coa_data.json"
    if not data_file.exists():
        raise FileNotFoundError(f"CoA data file not found: {data_file}")

    with open(data_file, 'r') as f:
        data = json.load(f)

    COA_ACCOUNTS.clear()
    COA_NAME_INDEX.clear()
    REVENUE_ACCOUNTS.clear()
    DIRECT_COST_ACCOUNTS.clear()
    OPERATING_EXPENSE_ACCOUNTS.clear()
    OTHER_EXPENSE_ACCOUNTS.clear()
    OTHER_INCOME_EXPENSE_ACCOUNTS.clear()

    for item in data:
        account = COAAccount(
            code=item["code"],
            name=item["name"],
            description=item.get("description", "")
        )

        COA_ACCOUNTS[account.code] = account
        COA_NAME_INDEX[account.name.lower()] = account.code

        # Index aliases
        for alias in account.aliases:
            COA_NAME_INDEX[alias.lower()] = account.code

        # Categorize
        if account.code.startswith("5"):
            REVENUE_ACCOUNTS[account.code] = account
        elif account.code.startswith("6"):
            DIRECT_COST_ACCOUNTS[account.code] = account
        elif account.code.startswith("7"):
            OPERATING_EXPENSE_ACCOUNTS[account.code] = account
        elif account.code.startswith("8"):
            OTHER_EXPENSE_ACCOUNTS[account.code] = account
        elif account.code.startswith("9"):
            OTHER_INCOME_EXPENSE_ACCOUNTS[account.code] = account


def get_account_by_code(code: str) -> Optional[COAAccount]:
    """Get account by 4-digit code."""
    return COA_ACCOUNTS.get(code)


def get_accounts_by_series(series: str) -> List[COAAccount]:
    """Get all accounts in a series (e.g., '5000' for all revenue)."""
    return [acc for acc in COA_ACCOUNTS.values() if acc.series == series]


def get_accounts_by_category(category: str) -> List[COAAccount]:
    """Get all accounts in a category (e.g., 'Revenue', 'Direct Cost')."""
    return [acc for acc in COA_ACCOUNTS.values() if acc.category == category]


def search_accounts(query: str) -> List[COAAccount]:
    """
    Search accounts by name or alias.

    Args:
        query: Search string (case-insensitive)

    Returns:
        List of matching accounts
    """
    query_lower = query.lower()
    results = []

    # Check name match
    if query_lower in COA_NAME_INDEX:
        code = COA_NAME_INDEX[query_lower]
        results.append(COA_ACCOUNTS[code])

    # Check partial matches
    for code, account in COA_ACCOUNTS.items():
        if query_lower in account.name.lower() or query_lower in account.description.lower():
            if account not in results:
                results.append(account)

    return results


def serialize_coa_for_prompt(include_descriptions: bool = True) -> str:
    """
    Serialize CoA for inclusion in LLM prompt.

    Args:
        include_descriptions: Whether to include PDF field definitions

    Returns:
        Formatted string for prompt context
    """
    lines = []

    # Revenue accounts
    lines.append("=== REVENUE ACCOUNTS (5000 series) ===")
    for code in sorted(REVENUE_ACCOUNTS.keys()):
        acc = REVENUE_ACCOUNTS[code]
        if include_descriptions and acc.description:
            lines.append(f"{code} - {acc.name}: {acc.description[:200]}")
        else:
            lines.append(f"{code} - {acc.name}")

    # Direct costs
    lines.append("\n=== DIRECT COSTS (6000 series) ===")
    for code in sorted(DIRECT_COST_ACCOUNTS.keys()):
        acc = DIRECT_COST_ACCOUNTS[code]
        if include_descriptions and acc.description:
            lines.append(f"{code} - {acc.name}: {acc.description[:200]}")
        else:
            lines.append(f"{code} - {acc.name}")

    # Operating expenses
    lines.append("\n=== OPERATING EXPENSES (7000 series) ===")
    for code in sorted(OPERATING_EXPENSE_ACCOUNTS.keys()):
        acc = OPERATING_EXPENSE_ACCOUNTS[code]
        if include_descriptions and acc.description:
            lines.append(f"{code} - {acc.name}: {acc.description[:200]}")
        else:
            lines.append(f"{code} - {acc.name}")

    # Other expenses (depreciation / amortization)
    lines.append("\n=== OTHER EXPENSES - DEPRECIATION / AMORTIZATION (8000 series) ===")
    for code in sorted(OTHER_EXPENSE_ACCOUNTS.keys()):
        acc = OTHER_EXPENSE_ACCOUNTS[code]
        if include_descriptions and acc.description:
            lines.append(f"{code} - {acc.name}: {acc.description[:200]}")
        else:
            lines.append(f"{code} - {acc.name}")

    # Other income / other expense / interest / taxes
    lines.append("\n=== OTHER INCOME / OTHER EXPENSE / INTEREST / TAXES (9000 series) ===")
    for code in sorted(OTHER_INCOME_EXPENSE_ACCOUNTS.keys()):
        acc = OTHER_INCOME_EXPENSE_ACCOUNTS[code]
        if include_descriptions and acc.description:
            lines.append(f"{code} - {acc.name}: {acc.description[:200]}")
        else:
            lines.append(f"{code} - {acc.name}")

    return "\n".join(lines)


# Initialize on module load
_load_accounts()
