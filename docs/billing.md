# Customer billing setup

Billing stays configuration-required until an operator configures Stripe and a dated exchange rate. Configuration alone creates no Stripe resources and charges no payments. Opening a success return URL never grants credits.

Customer policy: payment is required before workspace use. There is no free trial. The verified owner bypasses customer payment and credit deductions. Other accounts are redirected to billing until a signed paid invoice or paid checkout establishes a paid period or purchased credits. Web/API/MCP and background workers enforce this server-side; old free credits cannot unlock or fund work. Customers may still open billing/account settings and sign out. A paid subscription retains workspace access through its confirmed paid period even when application credits are exhausted; new applications still need available credits.

Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `BILLING_PUBLIC_URL` in the service environment. The public URL must use HTTPS and must be the canonical application URL; it is never derived from the browser Host header. Keep secret values out of source control. Use Stripe test-mode credentials and CLI forwarding for deployment verification before enabling live mode.

When using `sk_test_` or `rk_test_`, checkout is restricted to account IDs explicitly listed in the server-side `BILLING_TEST_ACCOUNT_IDS` setting (comma-separated). Ordinary public customer accounts cannot use sandbox payments to unlock access. The billing page clearly labels test mode. Owner access remains free. Live activation requires a live API key and a signing secret from the corresponding live-mode webhook; test webhook secrets do not configure live event delivery.

Save `billing-fx.json` under `JOBPILOT_HOME` with `pln_per_usd` (a positive decimal string), `date` (ISO calendar date), and `source` (the operator's actual source attribution). Supply an actual observed exchange rate, not a placeholder. Flask deployments can instead set `BILLING_FX_RATE` to the same mapping. Every displayed subscription price includes 20 PLN converted to USD once per billing package. Top-ups grant one application credit for each $0.10 of base value (minimum $5); the markup buys no credits.

Register a Stripe **snapshot** webhook at `/billing/webhook` and use API version `2025-02-24.acacia` to match the explicitly pinned REST request contract. Subscribe to:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `invoice.paid` (the handler also accepts `invoice.payment_succeeded` and deduplicates the invoice)
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Configure the Stripe customer portal in the same Stripe account and mode. Enable payment-method management, invoice access, and cancellation. Keep portal subscription product/price switching disabled: checkout creates server-priced packages dynamically, and this implementation does not authorize arbitrary provider-side plan changes. Customers with a subscription manage it through the portal rather than creating an additional subscription through checkout.

The only public, CSRF-exempt endpoint is the signed webhook. Customer checkout and portal routes require authenticated sessions and CSRF. Owner bypass uses the authenticated server role, never submitted form flags. Both the provider API and hosted destination hosts are fixed; browser-supplied customer IDs, prices and redirect URLs are ignored.

The SQLite ledger is shared across the web and application worker. Checkout snapshots and stable provider idempotency keys survive network retries. Wallet grants wait for paid Checkout events; subscription grants wait for paid invoices. Quarterly and annual payments authorize monthly buckets through their paid service period. Buckets are materialized on ledger balance/reservation access, so missed months do not accumulate. Original calendar anchors are preserved across short months. Wallet buckets do not expire; subscription refunds preserve the original expiry. Reservations and settlements use immediate SQLite transactions.

No additional Python dependencies are required: Stripe REST uses the standard library with a 20-second timeout. Before live activation, verify test-mode hosted checkout, asynchronous success, invoice service periods, duplicate and reordered webhook delivery, cancellation, portal configuration, and a real reverse-proxy callback. Local automated tests mock provider responses and do not validate live Stripe credentials or account configuration.

Primary contract references: [Checkout Session creation](https://docs.stripe.com/api/checkout/sessions/create), [signed webhook delivery and ordering](https://docs.stripe.com/webhooks), [customer portal sessions](https://docs.stripe.com/api/customer_portal/sessions/create).
