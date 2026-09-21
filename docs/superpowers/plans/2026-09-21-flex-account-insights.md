# Flex account insights implementation plan

Date: 2026-09-21

## Scope and contracts

Expose the unused Flex information identified in the account review, and add
independent checks without changing canonical accounting or duplicating events.
Use a dedicated Account insights page and report, linked from shared navigation.
Reuse raw artifacts, the existing portfolio repository, Flex date normalization,
and the existing FIFO projections. SQL stays in the database layer; calculations
are pure analytics. No new database tables are required initially.

Select only successfully published artifacts for the configured account. Select
whole section versions, never merge corrected same-day holdings. Preserve section
dates, periods, source IDs and missing/empty distinctions. Missing values are
unknown, not zero. Use Decimal for money. Never sum currency totals and base
summaries, summary rows and detail rows, or overlapping report windows.

Assumptions: this is implementation and local validation, not deployment; existing
portfolio profit and return-on-net-transfers retain their meanings. Broker TWR,
MTM, cumulative FIFO gains, cash income, and pending income remain distinct.

## Sequential implementation and commit gates

Each step includes API/UI, focused regression tests, maintained documentation,
independent agent review, fixes and a separate commit. Do not start the next step
until the current step is reviewed and committed.

1. **Broker NAV, history and ChangeInNAV.** Add successful-source read model,
   account insights API/page, NAV components and cash/positions/accrual bridge,
   dated NAV chart and accessible table, period change breakdown and broker TWR.
   Verify actual newly exported ChangeInNAV, same-date checks, corrections,
   missing inputs, unsupported components and no double-counting of lending.
2. **Independent calculation checks.** Compare signed FIFO lots and remaining
   basis with broker positions; compare like-for-like P&L with broker FIFO/MTM
   summaries and cash movements with CashReport. Show matched/different/not
   comparable with reasons, periods and source references. Avoid circular checks
   against broker-authoritative snapshot quantities or fallback unrealized P&L.
   Verify missing history, stale projections, shorts, FX and period alignment.
3. **Pending income.** Upcoming dividends (gross, tax, fees, net, ex/pay dates),
   interest accrual rollforward, and action-linked payment/reversal checks.
   Verify corrections, partial/missing matches, no conflation with paid income,
   and rounding. Uncertain links remain explicitly unverified.
4. **Securities lending.** Owned/lent/borrowed/net shares, lent percentage and
   collateral context. Verify signed quantities, net-share identity and matching
   dates; lending collateral is not additional investment profit or spending cash.
5. **Settled cash.** Per-currency ending, settled and unsettled cash plus period.
   Verify currency/base-summary exclusion, missing values and negative balances.
   Do not label settled cash as buying power.
6. **Option lifecycle.** Assignment/exercise/expiration rows linked to canonical
   trades and stock history, with quantity/cash checks when identity is unique.
   Verify multipliers, missing/ambiguous legs and no duplicate trade creation.
7. **Concentration.** Signed broker NAV weights, largest holdings, asset/currency
   allocation and broker-versus-calculated weight check. Label option market-value
   weight separately from underlying risk. Verify shorts, zero/missing NAV and FX.
8. **Commission details.** Broker/exchange/clearing/regulatory components, totals
   and execution-linked commission checks. Verify signed rebates, currencies,
   missing coverage, duplicate reports and regulatory subtotal overlap.

## Final validation

Run full pytest, Ruff and MyPy, plus the isolated PostgreSQL seeded release gate.
Exercise new database queries against isolated seeded data and read-only live
reports; execute UI JavaScript tests including empty/error states and safe text
rendering. Record each independent review and commit in this plan. No private
account figures or raw reports are committed as test fixtures.

## Progress

- Planning: fresh export completed successfully; ChangeInNAV is now present.
- Step 1 complete: independent review approved after fixing missing mode inputs,
  summary/lot selection and normalized history deduplication. Twelve focused
  analytics/UI/PostgreSQL tests pass; Ruff and MyPy pass. Fresh live export verified.
- Step 2: independent review findings addressed (nullable deductions, freshness of
  both period boundaries, ambiguous position detail). Focused analytics/UI and
  PostgreSQL checks pass; live NAV and ChangeInNAV comparisons match. Ledger
  checks explicitly distinguish independent evidence from broker arithmetic.
- Step 3: pending dividends, interest rollforward and historical payment checks
  implemented. Live export contains action IDs; missing identities remain visible
  as unverified. Review fixes preserve ambiguous entitlement groups and canonical
  cash deductions. Eight focused income/UI tests, Ruff and MyPy pass.
- Step 4 complete: independent review approved signed securities-lending view.
  Six holdings/UI tests, Ruff and MyPy pass; ownership and collateral are distinct.
- Step 5 complete: independent review approved native settled/unsettled cash.
  Eight holdings/UI tests, Ruff and MyPy pass.
- Step 6 complete: independent review approved option lifecycle links and checks.
  Live report's two assignments and their stock legs match; focused option/UI
  tests, Ruff and MyPy pass. Expirations do not invent delivery legs.
- Step 7 complete: independently reviewed after live validation corrected the
  misleading IBKR percentOfNAV name: broker percentages use asset-class totals.
  Account NAV weights remain separate. Live checks: 101 matched, two unavailable.
  Twelve holdings/UI tests, Ruff and MyPy pass.
- Step 8 complete: independent review approved commission components, execution
  links and coverage. Eleven commission/UI tests pass. Live data: ten component
  sums match, nine execution comparisons match and one differs. Ruff/MyPy pass.

## Completion and validation

All eight steps were independently reviewed, findings fixed and committed before
starting the next step:

| Step | Commit |
| --- | --- |
| NAV/history/ChangeInNAV | e8e18b8 |
| Independent calculation checks | afab0f7 |
| Pending income | 1f3fa53 |
| Securities lending | 1369853 |
| Settled cash | fd4c9e5 |
| Option lifecycle | d3f0651 |
| Concentration | 549c138 |
| Commission details | a586561 |

A second independent integration reviewer identified two UI issues: mismatch
values were hidden in some panels, and lending exceptions could be filtered out.
Both were fixed and approved with executable UI regressions. Calculation checks
show exceptions first with a matched toggle and preserve eight-decimal quantities.
Large panels scroll; source identities are expandable.

Validation on 2026-09-21:

- Full repository suite after all eight features: 884 passed.
- Final focused account-insights suite, including PostgreSQL and final UI fixes:
  44 passed, no skips.
- Explicit seeded release gate plus account-insights PostgreSQL regression:
  11 passed, no skips.
- Ruff, MyPy and git diff whitespace check pass.
- Read-only live API/UI smoke checks: HTTP 200; monetary JSON values remain strings.
- Fresh export imported the newly configured ChangeInNAV successfully.

User guide: ../../account_insights.md. Implemented on codex/flex-account-insights;
no deployment, push, pull request or merge was performed.
