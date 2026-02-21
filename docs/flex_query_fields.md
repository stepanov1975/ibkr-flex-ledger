# IBKR Flex Query Field Catalog

Generated on 2026-02-21 from project reference repositories and IBKR guide terminology.

## Scope and sources

- Scope: envelope fields plus core Flex sections used by this project (`Trades`, `OpenPositions`, `CashTransactions`, `CorporateActions`, `SecuritiesInfo`, `ConversionRates`, `AccountInformation`).
- Primary schema source: `references/ibflex2/ibflex/Types.py` (field definitions and inline notes).
- Supporting reference: `references/ngv_reports_ibkr/README.md` (query configuration and full-field guidance).
- IBKR documentation anchor: Reporting Reference guide root and Flex Web Service v3 docs linked from `references/ibflex2/ibflex/parser.py` and `references/ibflex2/ibflex/client.py`.

## Flex query response envelope (`FlexQueryResponse`)

| Field | Type | Description |
| --- | --- | --- |
| `queryName` | `str` | Flex query name configured in IBKR Client Portal. |
| `type` | `str` | Record or action type code defined by the section context. |
| `FlexStatements` | `Tuple["FlexStatement", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `Message` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |

## Flex statement envelope and included sections (`FlexStatement`)

| Field | Type | Description |
| --- | --- | --- |
| `accountId` | `str` | IBKR account identifier for the statement/account context. |
| `fromDate` | `datetime.date` | Start date of statement range requested in Flex query. |
| `toDate` | `datetime.date` | End date of statement range requested in Flex query. |
| `period` | `str` | IBKR statement period label associated with this statement. |
| `whenGenerated` | `datetime.datetime` | Timestamp when IBKR generated the statement payload. |
| `AccountInformation` | `Optional["_AccountInformation"]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ChangeInNAV` | `Optional["_ChangeInNAV"]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `CashReport` | `Tuple["CashReportCurrency", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `MTDYTDPerformanceSummary` | `Tuple["MTDYTDPerformanceSummaryUnderlying", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `MTMPerformanceSummaryInBase` | `Tuple["MTMPerformanceSummaryUnderlying", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `EquitySummaryInBase` | `Tuple["EquitySummaryByReportDateInBase", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `FIFOPerformanceSummaryInBase` | `Tuple["FIFOPerformanceSummaryUnderlying", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `FdicInsuredDepositsByBank` | `Tuple` | TODO |
| `StmtFunds` | `Tuple["StatementOfFundsLine", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ChangeInPositionValues` | `Tuple["ChangeInPositionValue", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `OpenPositions` | `Tuple["OpenPosition", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `NetStockPositionSummary` | `Tuple["NetStockPosition", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ComplexPositions` | `Tuple` | TODO |
| `FxPositions` | `Tuple["FxLot", ...]` | N.B. FXLot wrapped in FxLots |
| `Trades` | `Tuple["Trade", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `HKIPOSubscriptionActivity` | `Tuple` | TODO |
| `TradeConfirms` | `Tuple["TradeConfirm", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `TransactionTaxes` | `Tuple` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `OptionEAE` | `Tuple["_OptionEAE", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `PendingExcercises` | `Tuple` | TODO |
| `TradeTransfers` | `Tuple["TradeTransfer", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `FxTransactions` | `Tuple["FxTransaction", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `UnbookedTrades` | `Tuple` | TODO |
| `RoutingCommissions` | `Tuple` | TODO |
| `IBGNoteTransactions` | `Tuple` | TODO |
| `UnsettledTransfers` | `Tuple["UnsettledTransfer", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `UnbundledCommissionDetails` | `Tuple["UnbundledCommissionDetail", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `Adjustments` | `Tuple` | TODO |
| `PriorPeriodPositions` | `Tuple["PriorPeriodPosition", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `CorporateActions` | `Tuple["CorporateAction", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ClientFees` | `Tuple["ClientFee", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ClientFeesDetail` | `Tuple["_ClientFeesDetail", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `DebitCardActivities` | `Tuple["DebitCardActivity", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `SoftDollars` | `Tuple` | TODO |
| `CashTransactions` | `Tuple["CashTransaction", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `SalesTaxes` | `Tuple["SalesTax", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `CFDCharges` | `Tuple` | TODO |
| `InterestAccruals` | `Tuple["InterestAccrualsCurrency", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `TierInterestDetails` | `Tuple["TierInterestDetail", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `HardToBorrowDetails` | `Tuple["HardToBorrowDetail", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `HardToBorrowMarkupDetails` | `Tuple` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `SLBOpenContracts` | `Tuple["SLBOpenContract", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `SLBActivities` | `Tuple["SLBActivity", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `SLBFees` | `Tuple["SLBFee", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `Transfers` | `Tuple["Transfer", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ChangeInDividendAccruals` | `Tuple["_ChangeInDividendAccrual", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `OpenDividendAccruals` | `Tuple["OpenDividendAccrual", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `SecuritiesInfo` | `Tuple["SecurityInfo", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ConversionRates` | `Tuple["ConversionRate", ...]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `HKIPOOpenSubscriptions` | `Tuple` | TODO |
| `CommissionCredits` | `Tuple` | TODO |
| `StockGrantActivities` | `Tuple` | TODO |
| `SLBCollaterals` | `Tuple` | TODO |
| `IncentiveCouponAccrualDetails` | `Tuple` | TODO |
| `DepositsOnHold` | `Tuple` | TODO |

## AccountInformation section (`AccountInformation`)

| Field | Type | Description |
| --- | --- | --- |
| `accountId` | `Optional[str]` | IBKR account identifier for the statement/account context. |
| `acctAlias` | `Optional[str]` | Optional account alias configured in IBKR. |
| `model` | `Optional[str]` | Model portfolio identifier/sub-account model in IBKR. |
| `currency` | `Optional[str]` | Currency code of the record amount/value. |
| `name` | `Optional[str]` | Account holder or account display name. |
| `accountType` | `Optional[str]` | IBKR account type classification. |
| `customerType` | `Optional[str]` | IBKR customer classification for account. |
| `accountCapabilities` | `Tuple[str, ...]` | Capability codes enabled on the account. |
| `tradingPermissions` | `Tuple[str, ...]` | Trading permission codes for account. |
| `registeredRepName` | `Optional[str]` | Registered representative name on account. |
| `registeredRepPhone` | `Optional[str]` | Registered representative phone number. |
| `dateOpened` | `Optional[datetime.date]` | Account open date. |
| `dateFunded` | `Optional[datetime.date]` | Date account was funded. |
| `dateClosed` | `Optional[datetime.date]` | Account close date where applicable. |
| `street` | `Optional[str]` | Residential or mailing address component from account profile. |
| `street2` | `Optional[str]` | Residential or mailing address component from account profile. |
| `city` | `Optional[str]` | City component for account address fields. |
| `state` | `Optional[str]` | State/region component for account address fields. |
| `country` | `Optional[str]` | Country component for account address fields. |
| `postalCode` | `Optional[str]` | Postal code for account address fields. |
| `streetResidentialAddress` | `Optional[str]` | Residential or mailing address component from account profile. |
| `street2ResidentialAddress` | `Optional[str]` | Residential or mailing address component from account profile. |
| `cityResidentialAddress` | `Optional[str]` | Residential or mailing address component from account profile. |
| `stateResidentialAddress` | `Optional[str]` | Residential or mailing address component from account profile. |
| `countryResidentialAddress` | `Optional[str]` | Residential or mailing address component from account profile. |
| `postalCodeResidentialAddress` | `Optional[str]` | Residential or mailing address component from account profile. |
| `masterName` | `Optional[str]` | Master account name for advisor/master structures. |
| `ibEntity` | `Optional[str]` | IBKR legal entity serving the account. |
| `primaryEmail` | `Optional[str]` | Primary email registered for account. |
| `accountRepName` | `Optional[str]` | Account representative name. |
| `accountRepPhone` | `Optional[str]` | Account representative phone number. |
| `lastTradedDate` | `Optional[datetime.date]` | Most recent traded date reported by IBKR. |

## OpenPositions section row (`OpenPosition`)

| Field | Type | Description |
| --- | --- | --- |
| `side` | `Optional[enums.LongShort]` | Long/short side indicator for position or transaction direction. |
| `assetCategory` | `Optional[enums.AssetClass]` | IBKR asset class code for the instrument. |
| `subCategory` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `accountId` | `Optional[str]` | IBKR account identifier for the statement/account context. |
| `currency` | `Optional[str]` | Currency code of the record amount/value. |
| `fxRateToBase` | `Optional[decimal.Decimal]` | FX conversion rate from row currency into base currency. |
| `reportDate` | `Optional[datetime.date]` | Statement report date that includes this record. |
| `symbol` | `Optional[str]` | Instrument symbol/ticker from IBKR reporting. |
| `description` | `Optional[str]` | Human-readable instrument or transaction description text. |
| `conid` | `Optional[str]` | IBKR contract identifier (Conid) for the instrument. |
| `securityID` | `Optional[str]` | Security identifier value for configured ID type. |
| `cusip` | `Optional[str]` | CUSIP instrument identifier where available. |
| `isin` | `Optional[str]` | ISIN instrument identifier where available. |
| `figi` | `Optional[str]` | FIGI instrument identifier where available. |
| `multiplier` | `Optional[decimal.Decimal]` | Contract multiplier applied to quantity/price. |
| `position` | `Optional[decimal.Decimal]` | End-of-day position quantity from OpenPositions. |
| `markPrice` | `Optional[decimal.Decimal]` | End-of-day mark/valuation price in statement context. |
| `positionValue` | `Optional[decimal.Decimal]` | Valuation of position in row currency. |
| `openPrice` | `Optional[decimal.Decimal]` | Average/open reference price used by IBKR for lot. |
| `costBasisPrice` | `Optional[decimal.Decimal]` | Per-unit cost basis price. |
| `costBasisMoney` | `Optional[decimal.Decimal]` | Total cost basis amount. |
| `fifoPnlUnrealized` | `Optional[decimal.Decimal]` | Unrealized FIFO PnL amount from IBKR. |
| `levelOfDetail` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `openDateTime` | `Optional[datetime.datetime]` | Date-time value for this record in IBKR statement context. |
| `holdingPeriodDateTime` | `Optional[datetime.datetime]` | Date-time value for this record in IBKR statement context. |
| `securityIDType` | `Optional[str]` | Type of security identifier (e.g., ISIN/CUSIP). |
| `issuer` | `Optional[str]` | Issuer name or issuer field from IBKR. |
| `issuerCountryCode` | `Optional[str]` | Country code associated with issuer. |
| `underlyingConid` | `Optional[str]` | Underlying instrument Conid for derivative-style records. |
| `underlyingSymbol` | `Optional[str]` | Underlying symbol for derivative-style records. |
| `code` | `Tuple[enums.Code, ...]` | IBKR code flags attached to this row (multi-value). |
| `originatingOrderID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `originatingTransactionID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `accruedInt` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `acctAlias` | `Optional[str]` | Optional account alias configured in IBKR. |
| `model` | `Optional[str]` | Model portfolio identifier/sub-account model in IBKR. |
| `sedol` | `Optional[str]` | SEDOL instrument identifier where available. |
| `percentOfNAV` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `strike` | `Optional[decimal.Decimal]` | Option strike price. |
| `expiry` | `Optional[datetime.date]` | Contract expiry date. |
| `putCall` | `Optional[enums.PutCall]` | Option right indicator (put/call). |
| `principalAdjustFactor` | `Optional[decimal.Decimal]` | Corporate-action adjustment factor used by IBKR. |
| `listingExchange` | `Optional[str]` | Primary exchange code used in listing/reporting. |
| `underlyingSecurityID` | `Optional[str]` | Underlying security identifier. |
| `underlyingListingExchange` | `Optional[str]` | Underlying instrument listing exchange. |
| `positionValueInBase` | `Optional[decimal.Decimal]` | Valuation of position in base currency. |
| `unrealizedCapitalGainsPnl` | `Optional[decimal.Decimal]` | PnL field reported by IBKR for this record. |
| `unrealizedlFxPnl` | `Optional[decimal.Decimal]` | PnL field reported by IBKR for this record. |
| `vestingDate` | `Optional[datetime.date]` | Date value for this field in IBKR statement context. |
| `serialNumber` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `deliveryType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `commodityType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `fineness` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `weight` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |

## Trades section row (`Trade`)

| Field | Type | Description |
| --- | --- | --- |
| `transactionType` | `Optional[enums.TradeType]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `openCloseIndicator` | `Optional[enums.OpenClose]` | Open/close position intent indicator. |
| `buySell` | `Optional[enums.BuySell]` | Trade side indicator (buy or sell). |
| `orderType` | `Optional[enums.OrderType]` | Order type (for example MKT, LMT) from IBKR. |
| `assetCategory` | `Optional[enums.AssetClass]` | IBKR asset class code for the instrument. |
| `accountId` | `Optional[str]` | IBKR account identifier for the statement/account context. |
| `currency` | `Optional[str]` | Currency code of the record amount/value. |
| `fxRateToBase` | `Optional[decimal.Decimal]` | FX conversion rate from row currency into base currency. |
| `symbol` | `Optional[str]` | symbol of instrument traded, e.g. AAPL, not unique in IBKR as it can exist on different exchanges: (symbol, Exchange, Currency, Asset Type) is unique |
| `conid` | `Optional[str]` | IBKR identifier of instrument, unique key within IBKR |
| `cusip` | `Optional[str]` | S&P instrument ID, not unique as it is used on different exchanges |
| `isin` | `Optional[str]` | instrument ISIN (ISO standardized instrument ID) |
| `figi` | `Optional[str]` | instrument FIGI (Bloomberg ID - comparable to ISIN) |
| `description` | `Optional[str]` | instrument name, e.g. "Apple Inc." |
| `listingExchange` | `Optional[str]` | exchange, e.g. "NASDAQ" |
| `multiplier` | `Optional[decimal.Decimal]` | multiplier of contract traded |
| `strike` | `Optional[decimal.Decimal]` | Option strike price. |
| `expiry` | `Optional[datetime.date]` | Contract expiry date. |
| `putCall` | `Optional[enums.PutCall]` | Option right indicator (put/call). |
| `tradeID` | `Optional[str]` | IBKR trade identifier for trade-linked rows. |
| `reportDate` | `Optional[datetime.date]` | when the trade was included in IBKR's reporting system (e.g. corrections) |
| `tradeDate` | `Optional[datetime.date]` | date of the trade |
| `tradeTime` | `Optional[datetime.time]` | timestamp of the trade |
| `settleDateTarget` | `Optional[datetime.date]` | expected date of ownership transfer |
| `exchange` | `Optional[str]` | Execution exchange/venue for the trade. |
| `quantity` | `Optional[decimal.Decimal]` | Quantity of units/shares/contracts for the record. |
| `tradePrice` | `Optional[decimal.Decimal]` | Per-unit execution price. |
| `tradeMoney` | `Optional[decimal.Decimal]` | TradeMoney = Proceeds + Fees + Commissions |
| `proceeds` | `Optional[decimal.Decimal]` | Proceeds = Quantity * TradePrice * Multiplier |
| `netCash` | `Optional[decimal.Decimal]` | netCash = TradeMoney - Adjustments (e.g. fees in physical execution of options, or taxes) |
| `netCashInBase` | `Optional[decimal.Decimal]` | = NetCash × FX Rate |
| `taxes` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ibCommission` | `Optional[decimal.Decimal]` | Commission charged by IBKR for the transaction. |
| `ibCommissionCurrency` | `Optional[str]` | Currency of IBKR commission amount. |
| `closePrice` | `Optional[decimal.Decimal]` | closing market price of the asset on the trade date |
| `notes` | `Tuple[enums.Code, ...]` | separator = ";" |
| `cost` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `mtmPnl` | `Optional[decimal.Decimal]` | PnL at the time of reportins |
| `origTradePrice` | `Optional[decimal.Decimal]` | Price field emitted by IBKR for this record type. |
| `origTradeDate` | `Optional[datetime.date]` | Date value for this field in IBKR statement context. |
| `origTradeID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `origOrderID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `openDateTime` | `Optional[datetime.datetime]` | Date-time value for this record in IBKR statement context. |
| `fifoPnlRealized` | `Optional[decimal.Decimal]` | Realized FIFO PnL amount from IBKR. |
| `capitalGainsPnl` | `Optional[decimal.Decimal]` | Capital gains PnL amount from IBKR reporting. |
| `levelOfDetail` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `ibOrderID` | `Optional[str]` | IBKR order identifier associated with execution. |
| `orderTime` | `Optional[datetime.datetime]` | Time value for this field in IBKR statement context. |
| `changeInPrice` | `Optional[decimal.Decimal]` | Price field emitted by IBKR for this record type. |
| `changeInQuantity` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `fxPnl` | `Optional[decimal.Decimal]` | FX PnL component reported by IBKR. |
| `clearingFirmID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `transactionID` | `Optional[str]` | IBKR transaction identifier for this record. |
| `holdingPeriodDateTime` | `Optional[datetime.datetime]` | Date-time value for this record in IBKR statement context. |
| `ibExecID` | `Optional[str]` | IBKR execution identifier; execution-level trade key. |
| `brokerageOrderID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `orderReference` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `volatilityOrderLink` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `exchOrderId` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `extExecID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `traderID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `isAPIOrder` | `Optional[bool]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `acctAlias` | `Optional[str]` | Optional account alias configured in IBKR. |
| `model` | `Optional[str]` | some clients use model portfolios in account, i.e. virtual sub-accounts |
| `securityID` | `Optional[str]` | Security identifier value for configured ID type. |
| `securityIDType` | `Optional[str]` | Type of security identifier (e.g., ISIN/CUSIP). |
| `principalAdjustFactor` | `Optional[decimal.Decimal]` | relevant e.g. in stock splits |
| `dateTime` | `Optional[datetime.datetime]` | IBKR event timestamp for this record. |
| `underlyingConid` | `Optional[str]` | Underlying instrument Conid for derivative-style records. |
| `underlyingSecurityID` | `Optional[str]` | Underlying security identifier. |
| `underlyingSymbol` | `Optional[str]` | Underlying symbol for derivative-style records. |
| `underlyingListingExchange` | `Optional[str]` | Underlying instrument listing exchange. |
| `issuer` | `Optional[str]` | Issuer name or issuer field from IBKR. |
| `sedol` | `Optional[str]` | SEDOL instrument identifier where available. |
| `whenRealized` | `Optional[datetime.datetime]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `whenReopened` | `Optional[datetime.datetime]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `accruedInt` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `serialNumber` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `deliveryType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `commodityType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `fineness` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `weight` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `relatedTradeID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `relatedTransactionID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `origTransactionID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `subCategory` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `issuerCountryCode` | `Optional[str]` | Country code associated with issuer. |
| `rtn` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `initialInvestment` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `positionActionID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |

## CashTransactions section row (`CashTransaction`)

| Field | Type | Description |
| --- | --- | --- |
| `type` | `Optional[enums.CashAction]` | Record or action type code defined by the section context. |
| `assetCategory` | `Optional[enums.AssetClass]` | IBKR asset class code for the instrument. |
| `subCategory` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `accountId` | `Optional[str]` | IBKR account identifier for the statement/account context. |
| `currency` | `Optional[str]` | Currency code of the record amount/value. |
| `fxRateToBase` | `Optional[decimal.Decimal]` | FX conversion rate from row currency into base currency. |
| `description` | `Optional[str]` | Human-readable instrument or transaction description text. |
| `conid` | `Optional[str]` | IBKR contract identifier (Conid) for the instrument. |
| `securityID` | `Optional[str]` | Security identifier value for configured ID type. |
| `cusip` | `Optional[str]` | CUSIP instrument identifier where available. |
| `isin` | `Optional[str]` | ISIN instrument identifier where available. |
| `listingExchange` | `Optional[str]` | Primary exchange code used in listing/reporting. |
| `underlyingConid` | `Optional[str]` | Underlying instrument Conid for derivative-style records. |
| `underlyingSecurityID` | `Optional[str]` | Underlying security identifier. |
| `underlyingListingExchange` | `Optional[str]` | Underlying instrument listing exchange. |
| `amount` | `Optional[decimal.Decimal]` | Cash amount of transaction/corporate action. |
| `dateTime` | `Optional[datetime.datetime]` | IBKR event timestamp for this record. |
| `sedol` | `Optional[str]` | SEDOL instrument identifier where available. |
| `symbol` | `Optional[str]` | Instrument symbol/ticker from IBKR reporting. |
| `securityIDType` | `Optional[str]` | Type of security identifier (e.g., ISIN/CUSIP). |
| `underlyingSymbol` | `Optional[str]` | Underlying symbol for derivative-style records. |
| `issuer` | `Optional[str]` | Issuer name or issuer field from IBKR. |
| `multiplier` | `Optional[decimal.Decimal]` | Contract multiplier applied to quantity/price. |
| `strike` | `Optional[decimal.Decimal]` | Option strike price. |
| `expiry` | `Optional[datetime.date]` | Contract expiry date. |
| `putCall` | `Optional[enums.PutCall]` | Option right indicator (put/call). |
| `principalAdjustFactor` | `Optional[decimal.Decimal]` | Corporate-action adjustment factor used by IBKR. |
| `tradeID` | `Optional[str]` | IBKR trade identifier for trade-linked rows. |
| `code` | `Tuple[enums.Code, ...]` | IBKR code flags attached to this row (multi-value). |
| `transactionID` | `Optional[str]` | IBKR transaction identifier for this record. |
| `reportDate` | `Optional[datetime.date]` | Statement report date that includes this record. |
| `clientReference` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `settleDate` | `Optional[datetime.date]` | Date value for this field in IBKR statement context. |
| `acctAlias` | `Optional[str]` | Optional account alias configured in IBKR. |
| `actionID` | `Optional[str]` | Corporate action identifier assigned by IBKR. |
| `model` | `Optional[str]` | Model portfolio identifier/sub-account model in IBKR. |
| `levelOfDetail` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `serialNumber` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `deliveryType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `commodityType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `fineness` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `weight` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `figi` | `Optional[str]` | FIGI instrument identifier where available. |
| `issuerCountryCode` | `Optional[str]` | Country code associated with issuer. |
| `availableForTradingDate` | `Optional[datetime.datetime]` | Date value for this field in IBKR statement context. |
| `exDate` | `Optional[datetime.datetime]` | Date value for this field in IBKR statement context. |

## CorporateActions section row (`CorporateAction`)

| Field | Type | Description |
| --- | --- | --- |
| `assetCategory` | `Optional[enums.AssetClass]` | IBKR asset class code for the instrument. |
| `subCategory` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `accountId` | `Optional[str]` | IBKR account identifier for the statement/account context. |
| `currency` | `Optional[str]` | Currency code of the record amount/value. |
| `fxRateToBase` | `Optional[decimal.Decimal]` | FX conversion rate from row currency into base currency. |
| `symbol` | `Optional[str]` | Instrument symbol/ticker from IBKR reporting. |
| `description` | `Optional[str]` | Human-readable instrument or transaction description text. |
| `conid` | `Optional[str]` | IBKR contract identifier (Conid) for the instrument. |
| `securityID` | `Optional[str]` | Security identifier value for configured ID type. |
| `cusip` | `Optional[str]` | CUSIP instrument identifier where available. |
| `isin` | `Optional[str]` | ISIN instrument identifier where available. |
| `listingExchange` | `Optional[str]` | Primary exchange code used in listing/reporting. |
| `underlyingConid` | `Optional[str]` | Underlying instrument Conid for derivative-style records. |
| `underlyingSecurityID` | `Optional[str]` | Underlying security identifier. |
| `underlyingListingExchange` | `Optional[str]` | Underlying instrument listing exchange. |
| `actionID` | `Optional[str]` | Corporate action identifier assigned by IBKR. |
| `actionDescription` | `Optional[str]` | Corporate action narrative/description text. |
| `dateTime` | `Optional[datetime.datetime]` | IBKR event timestamp for this record. |
| `amount` | `Optional[decimal.Decimal]` | Cash amount of transaction/corporate action. |
| `quantity` | `Optional[decimal.Decimal]` | Quantity of units/shares/contracts for the record. |
| `fifoPnlRealized` | `Optional[decimal.Decimal]` | Realized FIFO PnL amount from IBKR. |
| `capitalGainsPnl` | `Optional[decimal.Decimal]` | Capital gains PnL amount from IBKR reporting. |
| `fxPnl` | `Optional[decimal.Decimal]` | FX PnL component reported by IBKR. |
| `mtmPnl` | `Optional[decimal.Decimal]` | Mark-to-market PnL amount in statement context. |
| `type` | `Optional[enums.Reorg]` | Record or action type code defined by the section context. |
| `code` | `Tuple[enums.Code, ...]` | IBKR code flags attached to this row (multi-value). |
| `sedol` | `Optional[str]` | SEDOL instrument identifier where available. |
| `acctAlias` | `Optional[str]` | Optional account alias configured in IBKR. |
| `model` | `Optional[str]` | Model portfolio identifier/sub-account model in IBKR. |
| `securityIDType` | `Optional[str]` | Type of security identifier (e.g., ISIN/CUSIP). |
| `underlyingSymbol` | `Optional[str]` | Underlying symbol for derivative-style records. |
| `issuer` | `Optional[str]` | Issuer name or issuer field from IBKR. |
| `multiplier` | `Optional[decimal.Decimal]` | Contract multiplier applied to quantity/price. |
| `strike` | `Optional[decimal.Decimal]` | Option strike price. |
| `expiry` | `Optional[datetime.date]` | Contract expiry date. |
| `putCall` | `Optional[enums.PutCall]` | Option right indicator (put/call). |
| `principalAdjustFactor` | `Optional[decimal.Decimal]` | Corporate-action adjustment factor used by IBKR. |
| `reportDate` | `Optional[datetime.date]` | Statement report date that includes this record. |
| `proceeds` | `Optional[decimal.Decimal]` | Gross proceeds amount from the transaction. |
| `value` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `transactionID` | `Optional[str]` | IBKR transaction identifier for this record. |
| `levelOfDetail` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `serialNumber` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `deliveryType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `commodityType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `fineness` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `weight` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |

## SecuritiesInfo section row (`SecurityInfo`)

| Field | Type | Description |
| --- | --- | --- |
| `assetCategory` | `Optional[enums.AssetClass]` | IBKR asset class code for the instrument. |
| `symbol` | `Optional[str]` | Instrument symbol/ticker from IBKR reporting. |
| `description` | `Optional[str]` | Human-readable instrument or transaction description text. |
| `conid` | `Optional[str]` | IBKR contract identifier (Conid) for the instrument. |
| `securityID` | `Optional[str]` | Security identifier value for configured ID type. |
| `cusip` | `Optional[str]` | CUSIP instrument identifier where available. |
| `isin` | `Optional[str]` | ISIN instrument identifier where available. |
| `listingExchange` | `Optional[str]` | Primary exchange code used in listing/reporting. |
| `underlyingSecurityID` | `Optional[str]` | Underlying security identifier. |
| `underlyingListingExchange` | `Optional[str]` | Underlying instrument listing exchange. |
| `underlyingConid` | `Optional[str]` | Underlying instrument Conid for derivative-style records. |
| `underlyingCategory` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `subCategory` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `multiplier` | `Optional[decimal.Decimal]` | Contract multiplier applied to quantity/price. |
| `strike` | `Optional[decimal.Decimal]` | Option strike price. |
| `expiry` | `Optional[datetime.date]` | Contract expiry date. |
| `maturity` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `issueDate` | `Optional[datetime.date]` | Date value for this field in IBKR statement context. |
| `type` | `Optional[str]` | Record or action type code defined by the section context. |
| `sedol` | `Optional[str]` | SEDOL instrument identifier where available. |
| `securityIDType` | `Optional[str]` | Type of security identifier (e.g., ISIN/CUSIP). |
| `underlyingSymbol` | `Optional[str]` | Underlying symbol for derivative-style records. |
| `issuer` | `Optional[str]` | Issuer name or issuer field from IBKR. |
| `putCall` | `Optional[enums.PutCall]` | Option right indicator (put/call). |
| `principalAdjustFactor` | `Optional[decimal.Decimal]` | Corporate-action adjustment factor used by IBKR. |
| `code` | `Tuple[enums.Code, ...]` | IBKR code flags attached to this row (multi-value). |
| `currency` | `Optional[str]` | Currency code of the record amount/value. |
| `settlementPolicyMethod` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `figi` | `Optional[str]` | FIGI instrument identifier where available. |
| `issuerCountryCode` | `Optional[str]` | Country code associated with issuer. |
| `relatedTradeID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `origTransactionID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `relatedTransactionID` | `Optional[str]` | Identifier field emitted by IBKR for linkage/deduplication. |
| `rtn` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `initialInvestment` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `serialNumber` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `deliveryType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `commodityType` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `fineness` | `Optional[decimal.Decimal]` | IBKR Flex field for this section; include when full audit fields are enabled. |
| `weight` | `Optional[str]` | IBKR Flex field for this section; include when full audit fields are enabled. |

## ConversionRates section row (`ConversionRate`)

| Field | Type | Description |
| --- | --- | --- |
| `reportDate` | `Optional[datetime.date]` | Statement report date that includes this record. |
| `fromCurrency` | `Optional[str]` | Source currency in ConversionRates row. |
| `toCurrency` | `Optional[str]` | Target currency in ConversionRates row. |
| `rate` | `Optional[decimal.Decimal]` | Conversion rate from fromCurrency to toCurrency. |

## Notes

- IBKR can include/exclude many fields per section depending on query configuration; optional fields may be absent in a payload.
- For deterministic ingestion in this project, enable all fields/columns for included sections (except explicitly excluded metal-delivery attributes where desired).
