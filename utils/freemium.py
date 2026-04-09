"""
Freemium model utilities for usage tracking and access control.

Free Tier: 2 extractions per month
Pro Tier: Unlimited extractions
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

FREE_TIER_LIMIT = 2  # extractions per month
PRO_TIER_LIMIT = -1  # unlimited


class UsageTracker:
    """Track user extractions for freemium model."""

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize usage tracker.

        Args:
            storage_path: Path to store usage data (default: .streamlit/usage.json)
        """
        if storage_path is None:
            from pathlib import Path
            storage_path = Path(__file__).parent.parent / ".streamlit" / "usage.json"

        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> Dict:
        """Load usage data from disk."""
        if self.storage_path.exists():
            with open(self.storage_path, "r") as f:
                return json.load(f)
        return {"users": {}}

    def _save(self):
        """Save usage data to disk."""
        with open(self.storage_path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get_user(self, email: str) -> Dict[str, Any]:
        """Get user data by email."""
        return self._data["users"].get(email, {
            "tier": "free",
            "extractions_this_month": 0,
            "month_start": datetime.now().strftime("%Y-%m"),
            "total_extractions": 0,
            "created_at": datetime.now().isoformat(),
        })

    def save_user(self, email: str, user_data: Dict):
        """Save user data."""
        self._data["users"][email] = user_data
        self._save()

    def increment_extraction(self, email: str) -> tuple[bool, int, int]:
        """
        Record an extraction and check if within limits.

        Returns:
            (allowed, current_count, limit)
        """
        user = self.get_user(email)

        # Reset counter if new month
        current_month = datetime.now().strftime("%Y-%m")
        if user.get("month_start") != current_month:
            user["extractions_this_month"] = 0
            user["month_start"] = current_month

        # Check limits
        tier = user.get("tier", "free")
        limit = PRO_TIER_LIMIT if tier == "pro" else FREE_TIER_LIMIT

        if limit != -1 and user["extractions_this_month"] >= limit:
            return False, user["extractions_this_month"], limit

        # Increment counter
        user["extractions_this_month"] += 1
        user["total_extractions"] += 1
        self.save_user(email, user)

        return True, user["extractions_this_month"], limit

    def upgrade_to_pro(self, email: str):
        """Upgrade user to Pro tier."""
        user = self.get_user(email)
        user["tier"] = "pro"
        user["upgraded_at"] = datetime.now().isoformat()
        self.save_user(email, user)

    def get_stats(self, email: str) -> Dict:
        """Get usage stats for a user."""
        user = self.get_user(email)
        tier = user.get("tier", "free")
        limit = PRO_TIER_LIMIT if tier == "pro" else FREE_TIER_LIMIT

        return {
            "tier": tier,
            "extractions_this_month": user.get("extractions_this_month", 0),
            "limit": limit,
            "unlimited": limit == -1,
            "total_extractions": user.get("total_extractions", 0),
            "month_start": user.get("month_start", ""),
        }


# -----------------------------------------------------------------------------
# Session state helpers (for Streamlit)
# -----------------------------------------------------------------------------

def init_usage_session(email: str = "anonymous"):
    """Initialize freemium usage in session state."""
    import streamlit as st

    if "usage_tracker" not in st.session_state:
        st.session_state["usage_tracker"] = UsageTracker()

    if "user_email" not in st.session_state:
        st.session_state["user_email"] = email

    if "extraction_allowed" not in st.session_state:
        st.session_state["extraction_allowed"] = True

    if "usage_stats" not in st.session_state:
        st.session_state["usage_stats"] = st.session_state["usage_tracker"].get_stats(email)


def check_extraction_limit() -> tuple[bool, Dict]:
    """
    Check if user can perform an extraction.

    Returns:
        (allowed, stats_dict)
    """
    import streamlit as st

    tracker: UsageTracker = st.session_state.get("usage_tracker")
    email = st.session_state.get("user_email", "anonymous")

    if not tracker:
        return True, {}

    allowed, current, limit = tracker.increment_extraction(email)
    stats = tracker.get_stats(email)
    st.session_state["usage_stats"] = stats
    st.session_state["extraction_allowed"] = allowed

    return allowed, stats


def render_usage_indicator():
    """Render usage indicator in sidebar."""
    import streamlit as st

    stats = st.session_state.get("usage_stats", {})
    if not stats:
        return

    tier = stats.get("tier", "free")
    current = stats.get("extractions_this_month", 0)
    limit = stats.get("limit", FREE_TIER_LIMIT)
    unlimited = stats.get("unlimited", False)

    # Tier badge
    if tier == "pro":
        st.markdown(
            "<div style='background: linear-gradient(90deg, #10b981, #059669); "
            "padding: 8px 12px; border-radius: 8px; text-align: center; "
            "color: white; font-weight: bold; margin: 10px 0;'>"
            "⭐ PRO MEMBER - Unlimited Extractions"
            "</div>",
            unsafe_allow_html=True
        )
    else:
        # Free tier progress
        remaining = max(0, limit - current) if limit != -1 else "∞"
        progress = current / limit if limit > 0 else 0

        st.markdown(f"**Free Tier**: {current}/{limit} extractions")
        st.progress(progress)

        if remaining == 0:
            st.error("⚠️ Limit reached!")
        else:
            st.caption(f"{remaining} remaining this month")

        # Show free tier limitations
        st.divider()
        st.markdown("**Free Tier Limits:**")
        st.markdown(
            "<div style='font-size: 0.85em; color: #666;'>",
            unsafe_allow_html=True
        )
        st.markdown("• 1 statement at a time")
        st.markdown("• No Excel/JSON downloads")
        st.markdown("• Watermark on results")
        st.markdown("</div>", unsafe_allow_html=True)

        # Upgrade CTA
        st.divider()
        st.markdown(
            "<div style='text-align: center; margin: 15px 0;'>",
            unsafe_allow_html=True
        )
        if st.button("⬆️ Upgrade to Pro - $29/mo", use_container_width=True, type="primary"):
            st.session_state["show_upgrade_modal"] = True
        st.markdown("</div>", unsafe_allow_html=True)
