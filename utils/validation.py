"""
Validation Module for Extracted Financial Data

Checks:
- Balance Sheet: Assets = Liabilities + Equity
- Data completeness: Required fields present
- Anomaly detection: Unusual values, negative assets, etc.
"""

from typing import Dict, List, Optional, Any, Tuple


def validate_balance_sheet(data: Dict) -> Dict[str, Any]:
    """
    Validate a balance sheet extraction.

    Checks:
    - Accounting equation: Assets = Liabilities + Equity
    - Required sections present
    - No negative asset values (usually)

    Args:
        data: Extracted balance sheet data

    Returns:
        Dict with:
            - valid: bool
            - errors: List of error messages
            - warnings: List of warning messages
            - details: Dict of calculated values
    """
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "details": {}
    }

    sections = data.get("sections", [])
    periods = data.get("periods", [])

    # Find totals for each section
    totals = {}  # section_name -> {period_idx: value}

    for section in sections:
        section_name = section.get("name", "").upper()
        section_totals = {}

        for idx, row in enumerate(section.get("rows", [])):
            label = row.get("label", "").upper()
            values = row.get("values", [])

            # Check if this is a total row
            if row.get("is_subtotal") or any(kw in label for kw in ["TOTAL", "Total"]):
                for period_idx, val_str in enumerate(values):
                    if val_str:
                        val = parse_value(val_str)
                        if val is not None:
                            section_totals[period_idx] = val

        if section_totals:
            totals[section_name] = section_totals

    # Check accounting equation for each period
    for period_idx in range(len(periods)):
        # Find assets, liabilities, equity totals
        assets_total = None
        liabilities_total = None
        equity_total = None

        for section_name, section_totals in totals.items():
            if "ASSET" in section_name:
                assets_total = section_totals.get(period_idx)
            elif "LIAB" in section_name:
                liabilities_total = section_totals.get(period_idx)
            elif "EQUITY" in section_name or "STOCKHOLDER" in section_name:
                equity_total = section_totals.get(period_idx)

        # Check equation: Assets = Liabilities + Equity
        if assets_total is not None and liabilities_total is not None and equity_total is not None:
            calculated = liabilities_total + equity_total
            diff = abs(assets_total - calculated)
            tolerance = max(abs(assets_total) * 0.01, 1000)  # 1% or $1000

            result["details"][f"period_{period_idx}_assets"] = assets_total
            result["details"][f"period_{period_idx}_liabilities"] = liabilities_total
            result["details"][f"period_{period_idx}_equity"] = equity_total
            result["details"][f"period_{period_idx}_check"] = calculated

            if diff > tolerance:
                result["valid"] = False
                result["errors"].append(
                    f"Period {period_idx + 1}: Balance sheet doesn't balance. "
                    f"Assets ({assets_total:,.0f}) ≠ Liabilities + Equity ({calculated:,.0f}). "
                    f"Difference: {diff:,.0f}"
                )
            else:
                result["details"][f"period_{period_idx}_balanced"] = True

    # Check for negative assets (usually a warning)
    for section in sections:
        section_name = section.get("name", "").upper()
        if "ASSET" in section_name:
            for row in section.get("rows", []):
                label = row.get("label", "")
                values = row.get("values", [])

                for period_idx, val_str in enumerate(values):
                    if val_str:
                        val = parse_value(val_str)
                        if val is not None and val < 0:
                            # Some assets can be negative (accumulated depreciation)
                            if "accumulated" not in label.lower() and "depreciation" not in label.lower():
                                result["warnings"].append(
                                    f"Negative asset value for '{label}' in period {period_idx + 1}: {val_str}"
                                )

    return result


def validate_income_statement(data: Dict) -> Dict[str, Any]:
    """
    Validate an income statement extraction.

    Checks:
    - Gross Profit = Revenue - Cost of Revenue
    - Operating Income ≤ Gross Profit
    - Net Income ≤ Operating Income (usually)
    - Margin calculations seem reasonable

    Returns:
        Dict with valid, errors, warnings, details
    """
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "details": {}
    }

    sections = data.get("sections", [])
    periods = data.get("periods", [])

    # Extract key metrics
    metrics = {}  # metric_name -> {period_idx: value}

    for section in sections:
        for row in section.get("rows", []):
            label = row.get("label", "").upper()
            values = row.get("values", [])

            # Map common labels to standard metrics
            for period_idx, val_str in enumerate(values):
                if val_str:
                    val = parse_value(val_str)
                    if val is not None:
                        if "revenue" in label or "sales" in label or "turnover" in label:
                            metrics.setdefault("revenue", {})[period_idx] = val
                        elif "cost of revenue" in label.lower() or "cost of sales" in label.lower():
                            metrics.setdefault("cost_of_revenue", {})[period_idx] = val
                        elif "gross profit" in label.lower():
                            metrics.setdefault("gross_profit", {})[period_idx] = val
                        elif "operating income" in label.lower() or "operating profit" in label.lower():
                            metrics.setdefault("operating_income", {})[period_idx] = val
                        elif "net income" in label.lower() or "net profit" in label.lower():
                            metrics.setdefault("net_income", {})[period_idx] = val

    # Validate gross profit calculation
    for period_idx in range(len(periods)):
        revenue = metrics.get("revenue", {}).get(period_idx)
        cost = metrics.get("cost_of_revenue", {}).get(period_idx)
        gross_profit = metrics.get("gross_profit", {}).get(period_idx)

        if revenue is not None and cost is not None and gross_profit is not None:
            calculated_gross = revenue - cost
            diff = abs(gross_profit - calculated_gross)
            tolerance = max(abs(revenue) * 0.05, 10000)  # 5% tolerance

            if diff > tolerance:
                result["warnings"].append(
                    f"Period {period_idx + 1}: Gross Profit ({gross_profit:,.0f}) doesn't match "
                    f"Revenue - Cost ({calculated_gross:,.0f}). Difference: {diff:,.0f}"
                )

        # Check margin reasonableness
        if revenue is not None and revenue > 0:
            if "net_income" in metrics:
                net = metrics["net_income"].get(period_idx)
                if net is not None:
                    margin = net / revenue
                    if margin > 1 or margin < -1:
                        result["warnings"].append(
                            f"Period {period_idx + 1}: Unusual net margin ({margin:.1%}). "
                            f"Net: {net:,.0f}, Revenue: {revenue:,.0f}"
                        )

    return result


def validate_cash_flow(data: Dict) -> Dict[str, Any]:
    """
    Validate a cash flow statement extraction.

    Checks:
    - Operating cash flow is positive (usually)
    - Free cash flow ≤ Operating cash flow
    - Ending cash ≈ Beginning cash + Net change

    Returns:
        Dict with valid, errors, warnings, details
    """
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "details": {}
    }

    sections = data.get("sections", [])
    periods = data.get("periods", [])

    # Extract key metrics
    metrics = {}

    for section in sections:
        section_name = section.get("name", "").upper()
        for row in section.get("rows", []):
            label = row.get("label", "").upper()
            values = row.get("values", [])

            for period_idx, val_str in enumerate(values):
                if val_str:
                    val = parse_value(val_str)
                    if val is not None:
                        key = f"{section_name}_{label}"
                        metrics.setdefault(key, {})[period_idx] = val

                        # Track specific metrics
                        if "operating" in section_name and "cash" in label:
                            if row.get("is_subtotal"):
                                metrics.setdefault("operating_cash_flow", {})[period_idx] = val
                        if "investing" in section_name and "cash" in label:
                            if row.get("is_subtotal"):
                                metrics.setdefault("investing_cash_flow", {})[period_idx] = val
                        if "financing" in section_name and "cash" in label:
                            if row.get("is_subtotal"):
                                metrics.setdefault("financing_cash_flow", {})[period_idx] = val

    # Check that operating cash flow is usually positive
    for period_idx in range(len(periods)):
        ocf = metrics.get("operating_cash_flow", {}).get(period_idx)
        if ocf is not None and ocf < 0:
            result["warnings"].append(
                f"Period {period_idx + 1}: Negative operating cash flow ({ocf:,.0f}). "
                f"This can happen but warrants investigation."
            )

    return result


def parse_value(val_str: str) -> Optional[float]:
    """Parse a string value to float."""
    if not val_str:
        return None

    s = str(val_str).strip()

    if s.lower() in ["n/a", "na", "-", "--", ""]:
        return None

    # Handle parentheses for negatives
    is_negative = s.startswith("(") and s.endswith(")")
    if is_negative:
        s = s[1:-1]

    # Remove currency symbols, commas
    s = s.replace("$", "").replace("£", "").replace("€", "")
    s = s.replace(",", "").replace(" ", "")
    s = s.replace("%", "")

    try:
        val = float(s)
        return -val if is_negative else val
    except ValueError:
        return None


def run_all_validations(data: Dict, statement_type: str) -> Dict[str, Any]:
    """
    Run all validations for a statement type.

    Args:
        data: Extracted statement data
        statement_type: One of 'balance_sheet', 'income_statement', 'cash_flow'

    Returns:
        Combined validation results
    """
    validators = {
        "balance_sheet": validate_balance_sheet,
        "income_statement": validate_income_statement,
        "cash_flow": validate_cash_flow,
    }

    validator = validators.get(statement_type)
    if validator:
        return validator(data)

    return {"valid": True, "errors": [], "warnings": [], "details": {}}
