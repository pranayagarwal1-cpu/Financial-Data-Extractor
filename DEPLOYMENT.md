# Deployment Guide - Phase 1 MVP

## Step 1: Create Stripe Payment Link

### A. Sign up for Stripe
1. Go to https://dashboard.stripe.com/register
2. Create account (use **Test Mode** initially)
3. Toggle to **Test Mode** (switch in top right)

### B. Create Product
1. Go to **Products** → **Add Product**
2. Fill in:
   - **Name**: `Pro Monthly Subscription`
   - **Description**: `Unlimited financial statement extractions, priority processing`
   - **Pricing**: $29 USD / month (recurring)
3. Click **Save**

### C. Create Payment Link
1. Click your product → **Create Payment Link**
2. Configure:
   - **Checkout options**: Standard checkout
   - **After checkout**: 
     - Uncheck "Show confirmation page"
     - Check "Redirect to your website"
     - URL: `https://your-app.streamlit.app` (we'll update this after deploy)
   - **Customer emails**: Collect
3. Click **Create Link**
4. Copy the **Payment Link URL** (looks like `https://buy.stripe.com/test_xxxxx`)

### D. Update Code
Replace the placeholder in `frontend.py` (line ~190):

```python
stripe_link = "https://buy.stripe.com/test_YOUR_ACTUAL_LINK"
```

Commit and push:
```bash
git add frontend.py
git commit -m "Add Stripe payment link"
git push
```

---

## Step 2: Deploy to Streamlit Cloud

### A. Connect Repository
1. Go to https://streamlit.io/cloud
2. Click **New App** (or **Deploy** button)
3. Authorize GitHub access if prompted
4. Select:
   - **Repository**: `pranayagarwal1-cpu/Financial-Data-Extractor`
   - **Branch**: `master`
   - **Main file path**: `frontend.py`

### B. Add Secrets
In Streamlit Cloud dashboard:
1. Click your app → **Settings**
2. Scroll to **Secrets**
3. Click **Add Secret**
4. Add these one by one:

```toml
# Copy from .streamlit/secrets.toml
STRIPE_WEBHOOK_SECRET = "whsec_xxx"  # Get from Stripe → Developers → Webhooks
PAID_USERS = ["user1@example.com", "user2@example.com"]
```

### C. Advanced Settings
1. Under **Advanced** → **Python Version**: 3.12
2. **Environment Variables**:
   ```
   DEFAULT_MODEL=qwen3.5:397b-cloud
   ENABLE_OBSERVABILITY=true
   ```

### D. Deploy!
1. Click **Save** and **Deploy**
2. Wait for deployment (~2-5 minutes)
3. Your app URL: `https://pranayagarwal1-cpu-financial-data-extractor-xxxxxx.streamlit.app`

---

## Step 3: Update Stripe Redirect URL

After deployment:
1. Copy your Streamlit app URL
2. Go back to Stripe → Payment Link → Edit
3. Update redirect URL to your Streamlit app
4. Save

---

## Step 4: Test the Flow

### Free Tier Test
1. Open your Streamlit app
2. Enter email: `test@example.com`
3. Upload a PDF
4. Extract (should work - counts as 1/2)
5. Upload another, extract (2/2)
6. Third extraction should show upgrade prompt

### Pro Tier Test (Test Mode)
1. Click "Upgrade to Pro"
2. Complete Stripe checkout with test card:
   - Card: `4242 4242 4242 4242`
   - Expiry: Any future date
   - CVC: Any 3 digits
3. After payment, manually upgrade user in `.streamlit/usage.json` (or implement webhook)

---

## Step 5: Webhook Setup (Optional - Auto Provisioning)

To automatically grant Pro access after payment:

### A. Get Webhook Secret
1. Stripe Dashboard → **Developers** → **Webhooks**
2. **Add Endpoint**
3. Endpoint URL: `https://YOUR-APP.streamlit.app/api/webhook`
4. Events to listen:
   - `checkout.session.completed`
5. Copy **Signing Secret** (whsec_xxxxx)

### B. Add to Secrets
In Streamlit Cloud → Settings → Secrets:
```toml
STRIPE_WEBHOOK_SECRET = "whsec_xxxxx"
```

### C. Create Webhook Handler
(Create `api/webhook.py` - Phase 2 task)

---

## Troubleshooting

### App won't deploy
- Check `requirements.txt` has all dependencies
- Ensure no syntax errors in `frontend.py`
- Check Streamlit logs in dashboard

### Ollama connection fails
- Streamlit Cloud can't access local Ollama
- Solution: Deploy Ollama on a cloud VM (RunPod, Lambda Labs)
- Set `OLLAMA_HOST` environment variable

### Usage tracking resets
- `.streamlit/usage.json` is local to each instance
- For production: Use database (Phase 2)

---

## Next Steps (Phase 2)

- [ ] User authentication (Clerk.dev)
- [ ] Database for usage tracking (PostgreSQL)
- [ ] Automatic Pro provisioning via Stripe webhook
- [ ] Self-hosted Ollama on GPU VM
- [ ] Queue system for batch processing
