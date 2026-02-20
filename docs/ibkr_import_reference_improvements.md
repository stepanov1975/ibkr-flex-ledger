# IBKR Import Improvements Verified Against Reference Implementations

Date: 2026-02-20

Scope: This document includes only cases where reference implementations are verifiably better in a specific implementation aspect.

1. Add transport-level timeout retry in Flex HTTP calls

	 - Why reference is better:
		 - `references/ibflex/ibflex/client.py` and `references/ibflex2/ibflex/client.py` retry `requests.get()` up to 3 times on timeout (`submit_request`).
		 - Current project (`app/adapters/flex_web_service.py`) executes one HTTP call per poll attempt in `_adapter_http_get`; timeout immediately raises `FlexAdapterTimeoutError`.
	 - How to improve this project:
		 - Add a small transport retry loop inside `_adapter_http_get` for timeout-only failures.
		 - Keep existing poll-level retry logic unchanged; transport retry should be fast and local (no long sleep), and still emit deterministic timeout diagnostics when exhausted.

2. Parse and retain IBKR request timestamp metadata from `SendRequest`

	 - Why reference is better:
		 - `ibflex`/`ibflex2` parse `FlexStatementResponse` timestamp including EST/EDT normalization in `parse_stmt_response`.
		 - Current project request parsing in `adapter_fetch_report` reads `Status`, `ReferenceCode`, `Url`, but ignores timestamp metadata.
	 - How to improve this project:
		 - Parse response timestamp from `SendRequest` and normalize to UTC ISO-8601.
		 - Store it in request-stage timeline details to improve traceability for broker-side latency investigations.

3. Support IBKR null sentinels for optional fields

	 - Why reference is better:
		 - `ibflex`/`ibflex2` treat `""`, `"-"`, `"--"`, and `"N/A"` as null for optional values (`make_optional`).
		 - Current project mapping optional field handling (`_mapping_optional_value`) treats only blank values as null; sentinel strings propagate and can fail decimal/timestamp parsing.
	 - How to improve this project:
		 - Centralize optional-value normalization to map known IBKR sentinels to `None`.
		 - Reuse that normalization for optional decimal, timestamp, and date fields so one sentinel policy applies consistently.

4. Expand date parsing coverage to known IBKR formats

	 - Why reference is better:
		 - `ibflex` date table supports `%m/%d/%Y`, `%m/%d/%y`, and `%d-%b-%y` in addition to ISO-like formats.
		 - Current `app/domain/flex_parsing.py` supports `%Y/%m/%d`, `%Y-%m-%d`, `%Y%m%d` and split candidates, but not these common alternatives.
	 - How to improve this project:
		 - Extend `domain_flex_parse_local_date` with `%m/%d/%Y`, `%m/%d/%y`, and `%d-%b-%y`.
		 - Keep fail-fast mapping behavior, but reduce avoidable contract failures caused by valid IBKR format configuration.

5. Normalize thousands separators in decimal parsing

	 - Why reference is better:
		 - `ibflex` converts decimals with comma stripping (`x.replace(",", "")`) before `Decimal` conversion.
		 - Current mapping decimal validation in `app/mapping/service.py` directly calls `Decimal(value)`, so locale-formatted numeric strings can fail.
	 - How to improve this project:
		 - Add deterministic decimal pre-normalization for IBKR numeric text (remove commas before validation).
		 - Apply only at mapping boundary helpers to avoid changing persisted canonical contract semantics.

6. Validate extracted XML row tags against known data-element schema

	 - Why reference is better:
		 - `ibflex` performs tag-to-type dispatch (`getattr(Types, elem.tag)`), which enforces known element tags and fails fast on unknown data elements.
		 - Current raw extraction (`app/jobs/raw_extraction.py`) accepts any child section and leaf tag under `FlexStatement` without schema-level filtering.
	 - How to improve this project:
		 - Add a configurable allowlist/registry of expected section and row tags.
		 - Record unknown tags as diagnostics (or fail preflight in strict mode) instead of silently mapping them into canonical input.

7. Replace fragile `source_row_ref` substring filters with explicit row-tag routing

	 - Why reference is better:
		 - `ibflex` routes by parsed element class/tag, not by string pattern checks.
		 - Current mapping in `app/mapping/service.py` uses `":Trade:"` and `":CorporateAction:"` substring checks to identify relevant rows, which can break if XML tag names change.
	 - How to improve this project:
		 - Persist row element tag explicitly during raw extraction (for example `row_tag` field), then map by structured fields (`section_name == "Trades" and row_tag == "Trade"`).
		 - Keep `source_row_ref` for traceability only, not for routing logic.

8. Replace dynamic SQL fragment interpolation patterns with fixed query templates

	 - Why reference is better (for this specific safety pattern):
		 - Reference DB code (for example `references/IB_Flex/importdata.py`) uses parameterized SQL values only and avoids dynamic SQL fragments.
		 - Current project has internal-only interpolated SQL fragments:
			 - `app/db/canonical_persistence.py`: `f"WHERE {where_clause}"`
			 - `app/db/ingestion_run.py`: `f"ORDER BY {order_by_clause}"`
	 - How to improve this project:
		 - Replace fragment interpolation with enumerated fixed query variants selected by validated mode/enum.
		 - Preserve allowlist checks, but remove interpolation pattern entirely to reduce future regression risk during refactors.

## Notes on excluded findings

- Some report items are valid project risks but are not clear "reference is better" cases (for example canonical instrument batching, broad orchestrator `RuntimeError` catch behavior, or protocol typing ergonomics). Those are intentionally excluded from this list to keep focus on the requested comparison criterion.
