# IBKR Import Improvements Derived from Verified Reference Comparison

Date: 2026-02-20

Scope rule for this document: include only cases where a reference implementation is verifiably better than the current project implementation.

## Verified conclusions summary

- `app/adapters/flex_web_service.py` already implements pooled `httpx.Client`, context-manager lifecycle, exponential backoff with jitter, typed adapter exceptions, and centralized Flex error-code enum/sets.
- `references/flexquery/flexquery/flexquery.py` and `references/IB_Flex/xml_downloader.py` are weaker than the project on poll correctness and library-grade error handling.
- The strongest reference advantage that remains is `ibflex`/`ibflex2` parser-time type validation and conversion discipline.

## Numbered improvements

1. Add explicit numeric field validation in mapping before database upsert

   Reference project:
   - `references/ibflex/ibflex/parser.py`
   - `references/ibflex2/ibflex/parser.py`

   Why reference is better:
   - `ibflex` converts numeric-like fields through typed converters (for example `decimal.Decimal`) and raises `FlexParserError` with class/field context when conversion fails.
   - Current project (`app/mapping/service.py`) accepts required numeric values as non-empty strings only, and `app/db/canonical_persistence.py` relies on PostgreSQL `CAST(... AS numeric)` at write time.
   - Result: malformed values such as `N/A` fail late as generic persistence errors, with weaker field-level diagnostics.

   How to improve this project:
   - Add mapping-layer helpers that validate and normalize decimal fields (`quantity`, `tradePrice`, `amount`, `rate`, and optional numeric fields).
   - Raise `MappingContractViolationError` including section, source row ref, field name, and raw value.
   - Keep raw payload immutable; validate only canonical-required fields so schema flexibility is preserved.

2. Enforce strict timestamp parsing for trade and cashflow event-time fields

   Reference project:
   - `references/ibflex/ibflex/parser.py` (`prep_datetime` + converter pipeline)
   - `references/ibflex2/ibflex/parser.py` (`prep_datetime` + converter pipeline)

   Why reference is better:
   - `ibflex` validates datetime strings during parse and fails early on malformed values.
   - Current project `_mapping_resolve_trade_timestamp` returns `dateTime` as any non-empty string, and cashflow `effective_at_utc` is passed through unvalidated.
   - Result: invalid timestamps can be persisted and only surface later in downstream processing.

   How to improve this project:
   - Add a shared mapping validator for timestamp fields that accepts the exact project-supported timestamp formats and emits normalized UTC ISO-8601.
   - Apply it to trade `dateTime` (required) and cashflow `effective_at_utc` (optional when present).
   - On failure, raise `MappingContractViolationError` with field-level context.

3. Consolidate date/time parsing logic into one shared validator module

   Reference project:
   - `references/ibflex/ibflex/parser.py`
   - `references/ibflex2/ibflex/parser.py`

   Why reference is better:
   - `ibflex` centralizes conversion logic (`prep_date`, `prep_datetime`, converter map), reducing drift between ingestion paths.
   - Current project has similar but separate parsing logic in `app/jobs/raw_extraction.py` (`_job_raw_try_parse_local_date`) and `app/mapping/service.py` (`_mapping_try_parse_report_date`).
   - Result: duplicated logic can diverge over time, causing subtle contract mismatches.

   How to improve this project:
   - Create one project-native parsing helper module for Flex date/dateTime normalization.
   - Reuse it from raw extraction, mapping, and any future reconciliation import paths.
   - Add focused tests that assert identical behavior across all consumers.

4. Add canonical field-contract validation boundary to improve fail-fast diagnostics

   Reference project:
   - `references/ibflex/ibflex/parser.py` (`parse_element_attr` checks known fields via `__annotations__`)
   - `references/ibflex2/ibflex/parser.py` (same pattern)

   Why reference is better:
   - `ibflex` fails fast when an attribute is unknown for the target typed class, preventing silent schema drift.
   - Current project intentionally preserves unknown fields in raw payloads (good for flexibility), but canonical mapping does not enforce a typed contract boundary for value domains (numeric/datetime/currency-like fields).
   - Result: some schema/value drift is caught late in persistence instead of at mapping boundary.

   How to improve this project:
   - Keep raw extraction permissive, but add an explicit canonical validation step immediately before canonical request construction.
   - Validate domain constraints only for fields the canonical schema depends on.
   - Emit deterministic mapping diagnostics while still retaining unknown raw attributes for future compatibility.

## Verification sources

- `app/adapters/flex_web_service.py`
- `app/adapters/flex_error_codes.py`
- `app/adapters/flex_errors.py`
- `app/jobs/raw_extraction.py`
- `app/mapping/service.py`
- `app/db/canonical_persistence.py`
- `references/ibflex/ibflex/parser.py`
- `references/ibflex2/ibflex/parser.py`
- `references/flexquery/flexquery/flexquery.py`
- `references/IB_Flex/xml_downloader.py`
