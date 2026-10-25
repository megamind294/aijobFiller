# Customer records and ten free applications implementation plan

**Goal:** Keep all existing customer/purchase records visible to the owner and give
new customer accounts exactly ten lifetime application credits.

**Architecture:** Preserve SQLite and the existing credit reservation/settlement
services. Enrollment is recorded atomically with each newly created account;
existing accounts are not enrolled. A separate `trial` credit kind avoids reviving
legacy free grants. Stable grant keys prevent repeat grants on sign-in or restart.
Trial credits unlock the normal workspace and guarded API/worker paths, but do
not grant the paid Power Auto Apply entitlement. Failures release reservations;
refunds retain trial provenance. Owner access remains unchanged.

**Scope decisions:** Ten credits are per account, not per day. Existing customers
receive no retroactive grant. Purchase history is derived from accepted payment
events, not checkout attempts. New payment events retain live/test provenance;
older events lacking that data are labeled unknown. The owner page is read-only,
paginated and restricted by the server-assigned owner role.

## Tasks

- [ ] Add failing trial tests: new signup and verified identities, one-time grant,
  legacy exclusion, ten successful applications/eleventh denial, reservation and
  refund behavior, parallel reservations, API and worker consistency.
- [ ] Implement enrollment and trial accounting; update browser/API/worker gates
  without treating free credits as a purchase.
- [ ] Add failing owner-list authorization/status/filter tests, then implement
  `/admin/customers`, navigation and live/test payment provenance.
- [ ] Update registration/billing copy and superseded paid-only regression fixtures.
- [ ] Run relevant tests, independent review and broader regression. Stage and test
  Linux source, back up production databases, deploy, then verify owner listing
  and one isolated signup without employer submission or payment.

## Recovery and evidence

Do not remove or rewrite existing accounts, credentials, credits or payment events.
Schema additions are additive. Save a production backup and previous release
pointer. Verify pre/post customer IDs and payment rows. A private synthetic signup
must be labeled as a test account and its unused free allowance is not proof of
real employer integration. Remaining employer adapter limitations still apply.
