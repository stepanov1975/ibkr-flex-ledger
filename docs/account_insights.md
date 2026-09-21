# Account insights

Open **Account insights** in the shared navigation (`/ui/account`). It reads
successfully imported Flex reports, without requesting live prices or changing
the ledger. Refresh reloads stored data; use ingestion to obtain a new export.

## Available information

| Panel | Purpose | Flex source |
| --- | --- | --- |
| Broker value/history | NAV components, cash/positions/accrual comparison and dated history | EquitySummaryInBase, CashReport, OpenPositions |
| Period change | Starting value, movements, ending value and broker TWR | ChangeInNAV |
| Calculation checks | FIFO quantity/basis/P&L, period gains, cash and MTM checks | Ledger plus position, FIFO/MTM and cash sections |
| Pending income | Expected dividends, tax, pay dates, accrued interest and payment evidence | OpenDividendAccruals, ChangeInDividendAccruals, InterestAccruals |
| Lending | Owned/lent/borrowed/net shares and collateral context | NetStockPositionSummary, EquitySummaryInBase |
| Cash settlement | Ending, settled and unsettled cash per currency | CashReport |
| Option lifecycle | Assignments/exercises/expirations, linked trades and delivery checks in the selected report | OptionEAE, canonical Trades |
| Concentration | Largest holdings, account and asset-class weights, securities allocation | OpenPositions, EquitySummaryInBase |
| Commission details | Signed components, execution comparisons and coverage | UnbundledCommissionDetails, canonical Trades |

Include these sections and their fields in Flex configuration, then import a new
report. Missing sections or fields are unavailable, not zero.
NAV and ChangeInNAV checks require all recognized components for the selected
calculation mode, including explicit zeroes. Missing component names appear in
the check explanation. Cash and MTM checks also run when OpenPositions is absent.

## Understanding checks

- **Matched:** values agree within tolerance: 0.01 monetary units, 0.000001 shares,
  or 0.01 percentage points, according to the check.
- **Different:** inspect the compared values and difference. Accounting methods,
  corporate actions and FX can explain differences; this does not repair the ledger.
- **Not comparable:** required inputs, a unique identity, matching dates, an exact
  opening snapshot, or fresh calculations are missing. The explanation states why.

Calculation checks show exceptions first. Enable **Show matched checks** for the
full list. Instrument links open existing activity pages. **Report sources** shows
selected statement dates and immutable source identities.

FIFO quantities come from signed remaining lots, not broker-authoritative snapshot
quantities. Basis and P&L checks require fresh inputs. Period gains require exact
opening/closing snapshots with cash income removed; missing opening values are
not zero. MTM component checks and lending identities are broker arithmetic, not
independent ledger reconstructions. Cash checks use reported opening cash plus
canonical transactions; unsupported FX cash legs remain not comparable.

## Distinctions

- Pending income is not paid cash. Reversals alone do not prove payment. Missing
  action identities and ambiguous entitlements remain unverified.
- Broker TWR applies to its stated period and is separate from cumulative profit
  or the dashboard's return on net transfers.
- IBKR `percentOfNAV` uses the **asset-class total**. Whole-account NAV weights
  are calculated separately. See the [IBKR definition](https://www.ibkrguides.com/reportingreference/reportguide/open%20positionsfq.htm).
- Option market-value weights are not delta exposure. Quotation currency is not
  economic currency risk. Securities allocation excludes cash and accruals.
- Lent shares remain investment holdings; collateral is not additional wealth or
  spending cash. Settled cash is not buying power or withdrawable funds.
- Commission charges are negative and rebates positive. Regulatory detail is
  already included in its subtotal. These amounts are not added to costs again.
  Coverage counts uniquely linked details, not whether every charge is correct.

Each section selects a whole successful report version. A newer same-day report
replaces that section, including an explicitly empty section. Omitted sections
can retain older dates; incompatible dates do not produce a match. NAV history
normalizes dates and prefers the newest successful source. Historical accruals
support payment checks without summing repeated snapshots.
