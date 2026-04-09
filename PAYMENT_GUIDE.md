# Payment & Monetization Setup Guide

## Step 1: Create Stripe Account

1. Go to https://stripe.com
2. Create a free account (test mode first)
3. Navigate to **Products** → **Add Product**

## Step 2: Create Pricing Tiers

### Free Tier
- Product name: `Free Tier`
- Price: $0
- Description: "2 extractions per month"

### Pro Tier
- Product name: `Pro Monthly`
- Price: $29/month
- Description: "Unlimited extractions, priority processing, Excel + JSON download"

### Pay-Per-Use (Optional)
- Product name: `Credit Pack - 10 Extractions`
- Price: $15 (one-time)
- Description: "10 extraction credits, no expiration"

## Step 3: Create Payment Links

For each paid product:

1. Go to **Products** → Click product → **Create Payment Link**
2. Configure:
   - Checkout options: Standard
   - After checkout: Redirect to your app URL
   - Customer emails: Collect
3. Copy the **Payment Link URL** (looks like: `https://buy.stripe.com/xxxxx`)

## Step 4: Add Payment Links to App

Update `frontend.py` sidebar with your Payment Link URLs:

```python
# Free users see upgrade prompt
if st.session_state.get("extractions_this_month", 0) >= 2:
    st.warning("Free tier limit reached (2/month)")
    if st.button("Upgrade to Pro - $29/month", use_container_width=True):
        st.markdown("[Click here to upgrade](https://buy.stripe.com/YOUR_LINK)")
```

## Step 5: Deploy to Streamlit Cloud

1. Go to https://streamlit.io/cloud
2. Click **New App**
3. Connect GitHub repo: `pranayagarwal1-cpu/Financial-Data-Extractor`
4. Main file path: `frontend.py`
5. Add secrets (copy from `.streamlit/secrets.toml`)
6. Click **Deploy!**

## Step 6: Webhook Setup (Optional - for automatic access)

To automatically grant access after payment:

1. In Stripe Dashboard → **Developers** → **Webhooks**
2. Add endpoint: `https://YOUR-APP.streamlit.app/api/webhook`
3. Select events: `checkout.session.completed`
4. Copy **Signing Secret** to `.streamlit/secrets.toml`

---

## Pricing Strategy Recommendations

| Tier | Price | Limits | Target |
|------|-------|--------|--------|
| Free | $0 | 2/month | Students, evaluators |
| Pro | $29/month | Unlimited | Individual analysts |
| Team | $99/month | 5 seats, unlimited | Small firms |
| Enterprise | Custom | API access, SLA | Large organizations |

## Next Steps (Phase 2)

- Add user accounts (Clerk.dev)
- Track usage per user
- Automatic provisioning after Stripe payment
- Usage dashboard per user
