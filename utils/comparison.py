"""
Peer Comparison Module

Aggregates extracted financial data from multiple companies
into a unified comparison table.

Usage:
    comparator = PeerComparator()
    comparator.add_company("Apple", apple_data)
    comparator.add_company("Microsoft", msft_data)
    comparison = comparator.build_comparison()
"""

import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StandardMetric:
    """A standardized financial metric for comparison."""
    name: str  # Display name
    category: str  # revenue, profitability, balance_sheet, etc.
    unit: str  # currency, percent, ratio
    higher_is_better: bool = True
    description: str = ""


# Standard metrics for peer comparison
STANDARD_METRICS = {
    # Revenue & Growth
    "revenue": StandardMetric(
        name="Revenue",
        category="Revenue & Growth",
        unit="currency",
        higher_is_better=True,
        description="Total revenue / net sales"
    ),
    "revenue_growth": StandardMetric(
        name="Revenue Growth (YoY)",
        category="Revenue & Growth",
        unit="percent",
        higher_is_better=True,
        description="Year-over-year revenue growth rate"
    ),

    # Profitability
    "gross_profit": StandardMetric(
        name="Gross Profit",
        category="Profitability",
        unit="currency",
        higher_is_better=True,
        description="Revenue minus cost of revenue"
    ),
    "gross_margin": StandardMetric(
        name="Gross Margin",
        category="Profitability",
        unit="percent",
        higher_is_better=True,
        description="Gross profit as % of revenue"
    ),
    "operating_income": StandardMetric(
        name="Operating Income",
        category="Profitability",
        unit="currency",
        higher_is_better=True,
        description="Income from operations"
    ),
    "operating_margin": StandardMetric(
        name="Operating Margin",
        category="Profitability",
        unit="percent",
        higher_is_better=True,
        description="Operating income as % of revenue"
    ),
    "net_income": StandardMetric(
        name="Net Income",
        category="Profitability",
        unit="currency",
        higher_is_better=True,
        description="Bottom line profit"
    ),
    "net_margin": StandardMetric(
        name="Net Margin",
        category="Profitability",
        unit="percent",
        higher_is_better=True,
        description="Net income as % of revenue"
    ),
    "ebitda": StandardMetric(
        name="EBITDA",
        category="Profitability",
        unit="currency",
        higher_is_better=True,
        description="Earnings before interest, taxes, depreciation & amortization"
    ),

    # Balance Sheet
    "total_assets": StandardMetric(
        name="Total Assets",
        category="Balance Sheet",
        unit="currency",
        higher_is_better=True,
        description="Sum of all assets"
    ),
    "total_liabilities": StandardMetric(
        name="Total Liabilities",
        category="Balance Sheet",
        unit="currency",
        higher_is_better=False,
        description="Sum of all liabilities"
    ),
    "total_equity": StandardMetric(
        name="Total Equity",
        category="Balance Sheet",
        unit="currency",
        higher_is_better=True,
        description="Shareholders' equity"
    ),
    "cash_and_equivalents": StandardMetric(
        name="Cash & Equivalents",
        category="Balance Sheet",
        unit="currency",
        higher_is_better=True,
        description="Cash and short-term investments"
    ),
    "total_debt": StandardMetric(
        name="Total Debt",
        category="Balance Sheet",
        unit="currency",
        higher_is_better=False,
        description="Short-term + long-term debt"
    ),

    # Financial Ratios
    "current_ratio": StandardMetric(
        name="Current Ratio",
        category="Ratios",
        unit="ratio",
        higher_is_better=True,
        description="Current assets / current liabilities"
    ),
    "debt_to_equity": StandardMetric(
        name="Debt-to-Equity",
        category="Ratios",
        unit="ratio",
        higher_is_better=False,
        description="Total debt / total equity"
    ),
    "return_on_equity": StandardMetric(
        name="Return on Equity (ROE)",
        category="Ratios",
        unit="percent",
        higher_is_better=True,
        description="Net income / shareholders' equity"
    ),
    "return_on_assets": StandardMetric(
        name="Return on Assets (ROA)",
        category="Ratios",
        unit="percent",
        higher_is_better=True,
        description="Net income / total assets"
    ),

    # Cash Flow
    "operating_cash_flow": StandardMetric(
        name="Operating Cash Flow",
        category="Cash Flow",
        unit="currency",
        higher_is_better=True,
        description="Cash from operating activities"
    ),
    "free_cash_flow": StandardMetric(
        name="Free Cash Flow",
        category="Cash Flow",
        unit="currency",
        higher_is_better=True,
        description="Operating cash flow minus capex"
    ),
}

# Metric name variations found in financial statements
METRIC_ALIASES = {
    # Revenue
    "revenue": ["revenue", "total revenue", "net revenue", "net sales", "sales", "turnover"],
    "revenue_growth": ["revenue growth", "sales growth", "growth rate", "yoy growth"],

    # Profitability
    "gross_profit": ["gross profit", "gross margin"],
    "gross_margin": ["gross margin", "gross profit margin", "gross %"],
    "operating_income": ["operating income", "operating profit", "income from operations", "operating earnings"],
    "operating_margin": ["operating margin", "operating profit margin"],
    "net_income": ["net income", "net profit", "net earnings", "profit for the year", "profit for the period"],
    "net_margin": ["net margin", "net profit margin", "profit margin"],
    "ebitda": ["ebitda", "adjusted ebitda"],

    # Balance Sheet
    "total_assets": ["total assets", "assets"],
    "total_liabilities": ["total liabilities", "liabilities", "total debt and equity"],
    "total_equity": ["total equity", "shareholders' equity", "stockholders' equity", "equity", "net assets"],
    "cash_and_equivalents": ["cash and equivalents", "cash", "cash and cash equivalents", "cash & equivalents"],
    "total_debt": ["total debt", "debt", "borrowings", "total borrowings"],

    # Cash Flow
    "operating_cash_flow": ["operating cash flow", "cash from operations", "net cash from operating"],
    "free_cash_flow": ["free cash flow", "fcf", "free cash"],
}


class PeerComparator:
    """
    Aggregates and compares financial data across companies.

    Usage:
        comparator = PeerComparator()
        comparator.add_company("Apple", apple_extracted_data)
        comparator.add_company("Microsoft", msft_extracted_data)
        comparison = comparator.build_comparison()
    """

    def __init__(self):
        self.companies: Dict[str, Dict] = {}
        self.fiscal_year: Optional[str] = None

    def add_company(self, company_name: str, extracted_data: Dict,
                    fiscal_year: Optional[str] = None):
        """
        Add a company's extracted financial data.

        Args:
            company_name: Display name for the company
            extracted_data: Dict from extraction workflow with keys:
                - company_name
                - fiscal_year
                - balance_sheet
                - income_statement
                - cash_flow
            fiscal_year: Override fiscal year if not in data
        """
        self.companies[company_name] = extracted_data

        if fiscal_year:
            self.fiscal_year = fiscal_year
        elif extracted_data.get("fiscal_year") and not self.fiscal_year:
            self.fiscal_year = extracted_data["fiscal_year"]

    def build_comparison(self) -> Dict[str, Any]:
        """
        Build a comparison table across all added companies.

        Returns:
            Dict with:
                - companies: List of company names
                - fiscal_year: Period being compared
                - metrics: Dict of metric comparisons
                - calculated_ratios: Computed financial ratios
        """
        comparison = {
            "companies": list(self.companies.keys()),
            "fiscal_year": self.fiscal_year,
            "metrics": {},
            "calculated_ratios": {}
        }

        # Extract and normalize metrics for each company
        for company_name, data in self.companies.items():
            normalized = self._normalize_company_data(data)

            # Merge into comparison
            for metric_key, value in normalized.items():
                if metric_key not in comparison["metrics"]:
                    comparison["metrics"][metric_key] = {}
                comparison["metrics"][metric_key][company_name] = value

        # Calculate ratios
        comparison["calculated_ratios"] = self._calculate_ratios()

        return comparison

    def _normalize_company_data(self, data: Dict) -> Dict[str, Optional[float]]:
        """
        Extract standardized metrics from a company's extracted data.

        Maps various label names to standard metric keys.
        """
        normalized = {}

        # Process income statement
        income_stmt = data.get("income_statement", {})
        for metric_key, aliases in METRIC_ALIASES.items():
            if metric_key in ["revenue_growth", "gross_margin", "operating_margin",
                             "net_margin", "current_ratio", "debt_to_equity",
                             "return_on_equity", "return_on_assets"]:
                continue  # These are calculated ratios

            value = self._find_metric_value(income_stmt, aliases)
            if value is not None:
                normalized[metric_key] = value

        # Process balance sheet
        balance_sheet = data.get("balance_sheet", {})
        for metric_key, aliases in METRIC_ALIASES.items():
            if metric_key in STANDARD_METRICS:
                if STANDARD_METRICS[metric_key].category != "Balance Sheet":
                    continue
                value = self._find_metric_value(balance_sheet, aliases)
                if value is not None and metric_key not in normalized:
                    normalized[metric_key] = value

        # Process cash flow
        cash_flow = data.get("cash_flow", {})
        for metric_key, aliases in METRIC_ALIASES.items():
            if metric_key in ["operating_cash_flow", "free_cash_flow"]:
                value = self._find_metric_value(cash_flow, aliases)
                if value is not None:
                    normalized[metric_key] = value

        return normalized

    def _find_metric_value(self, statement_data: Dict, aliases: List[str]) -> Optional[float]:
        """
        Find a metric value in statement data by trying multiple label names.

        Args:
            statement_data: Extracted statement data (sections with rows)
            aliases: List of possible label names for this metric

        Returns:
            Numeric value or None
        """
        sections = statement_data.get("sections", [])

        for section in sections:
            rows = section.get("rows", [])
            for row in rows:
                label = row.get("label", "").lower().strip()

                # Check if this row matches any alias
                for alias in aliases:
                    if alias in label or label in alias:
                        # Get the most recent period's value
                        values = row.get("values", [])
                        if values and values[0]:
                            return self._parse_value(values[0])

        return None

    def _parse_value(self, value_str: str) -> Optional[float]:
        """
        Parse a string value to float.

        Handles: "$1,234.56", "(123)", "12.3%", "N/A", etc.
        """
        if not value_str:
            return None

        # Clean the string
        s = str(value_str).strip()

        # Handle common non-numeric values
        if s.lower() in ["n/a", "na", "-", "--", ""]:
            return None

        # Handle parentheses for negatives: "(123)" -> -123
        is_negative = s.startswith("(") and s.endswith(")")
        if is_negative:
            s = s[1:-1]

        # Remove currency symbols, commas, spaces
        s = s.replace("$", "").replace("£", "").replace("€", "")
        s = s.replace(",", "").replace(" ", "")

        # Remove percentage sign (we'll handle it separately)
        is_percent = "%" in s
        s = s.replace("%", "")

        try:
            value = float(s)
            if is_negative:
                value = -value
            return value
        except ValueError:
            return None

    def _calculate_ratios(self) -> Dict[str, Dict[str, float]]:
        """
        Calculate financial ratios from raw metrics.

        Returns:
            Dict mapping ratio names to company -> value
        """
        ratios = {}

        # Get all companies
        companies = list(self.companies.keys())

        # Gross Margin
        ratios["gross_margin"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            gross_profit = data.get("gross_profit")
            revenue = data.get("revenue")
            if gross_profit and revenue and revenue != 0:
                ratios["gross_margin"][company] = round(gross_profit / revenue * 100, 1)

        # Operating Margin
        ratios["operating_margin"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            operating_income = data.get("operating_income")
            revenue = data.get("revenue")
            if operating_income is not None and revenue and revenue != 0:
                ratios["operating_margin"][company] = round(operating_income / revenue * 100, 1)

        # Net Margin
        ratios["net_margin"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            net_income = data.get("net_income")
            revenue = data.get("revenue")
            if net_income is not None and revenue and revenue != 0:
                ratios["net_margin"][company] = round(net_income / revenue * 100, 1)

        # Current Ratio
        ratios["current_ratio"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            # Would need current_assets and current_liabilities
            # Skip for now - requires more granular extraction

        # Debt-to-Equity
        ratios["debt_to_equity"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            total_debt = data.get("total_debt")
            total_equity = data.get("total_equity")
            if total_debt is not None and total_equity and total_equity != 0:
                ratios["debt_to_equity"][company] = round(total_debt / total_equity, 2)

        # Return on Equity
        ratios["return_on_equity"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            net_income = data.get("net_income")
            total_equity = data.get("total_equity")
            if net_income is not None and total_equity and total_equity != 0:
                ratios["return_on_equity"][company] = round(net_income / total_equity * 100, 1)

        # Return on Assets
        ratios["return_on_assets"] = {}
        for company in companies:
            data = self._get_normalized_for_company(company)
            net_income = data.get("net_income")
            total_assets = data.get("total_assets")
            if net_income is not None and total_assets and total_assets != 0:
                ratios["return_on_assets"][company] = round(net_income / total_assets * 100, 1)

        return ratios

    def _get_normalized_for_company(self, company_name: str) -> Dict[str, float]:
        """Get normalized metrics for a specific company."""
        data = self.companies.get(company_name, {})
        return self._normalize_company_data(data)

    def export_to_excel(self, output_path: str) -> str:
        """
        Export comparison table to Excel.

        Args:
            output_path: Path to save Excel file

        Returns:
            Path to saved file
        """
        import pandas as pd

        comparison = self.build_comparison()

        # Create summary sheet
        summary_data = {"Metric": [], "Company": [], "Value": [], "Unit": []}

        for metric_key, metric_info in STANDARD_METRICS.items():
            if metric_key in comparison["metrics"]:
                company_values = comparison["metrics"][metric_key]
                for company, value in company_values.items():
                    if value is not None:
                        summary_data["Metric"].append(metric_info.name)
                        summary_data["Company"].append(company)
                        summary_data["Value"].append(value)
                        summary_data["Unit"].append(metric_info.unit)

        df = pd.DataFrame(summary_data)

        # Pivot for wide format
        pivot_df = df.pivot_table(
            index="Metric",
            columns="Company",
            values="Value",
            aggfunc="first"
        ).reset_index()

        # Save to Excel
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            pivot_df.to_excel(writer, sheet_name="Comparison", index=False)

            # Add ratios sheet
            if comparison["calculated_ratios"]:
                ratios_data = {"Ratio": list(comparison["calculated_ratios"].keys())}
                for company in comparison["companies"]:
                    ratios_data[company] = [
                        comparison["calculated_ratios"][ratio].get(company, "")
                        for ratio in comparison["calculated_ratios"]
                    ]
                ratios_df = pd.DataFrame(ratios_data)
                ratios_df.to_excel(writer, sheet_name="Ratios", index=False)

        return output_path


def create_comparison_from_files(companies_data: Dict[str, str],
                                  output_dir: Path) -> Dict:
    """
    Create peer comparison from extracted JSON files.

    Args:
        companies_data: Dict mapping company name to extraction JSON path
        output_dir: Directory to save comparison outputs

    Returns:
        Comparison dict
    """
    comparator = PeerComparator()

    for company_name, json_path in companies_data.items():
        with open(json_path) as f:
            data = json.load(f)
        comparator.add_company(company_name, data)

    comparison = comparator.build_comparison()

    # Save comparison JSON
    comparison_path = output_dir / "peer_comparison.json"
    with open(comparison_path, "w") as f:
        json.dump(comparison, f, indent=2)

    # Save Excel
    excel_path = output_dir / "peer_comparison.xlsx"
    comparator.export_to_excel(str(excel_path))

    return comparison
