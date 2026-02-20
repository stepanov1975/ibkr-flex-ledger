# Reference Repositories Notes

Date: 2026-02-20
Purpose: Local study notes for reuse decisions in this project.

## Mandatory boundary

All code in `references/` is for reference only.

- It is not part of the project runtime.
- Do not import, execute, or wire this code directly into project modules.
- Use it only to inform architecture and implementation patterns in project-native code.

## Cloned repositories

- csingley/ibflex
- robcohen/ibflex2
- cubinet-code/flexquery
- alexpung/IB_Flex
- westonplatter/finx-reports-ib
- westonplatter/ngv_reports_ibkr
- Marfusios/premium-tracker

## What to reuse (patterns, not copy-paste)

### ibflex
- Reuse parser boundary design: keep Flex parsing isolated from accounting logic.
- Reuse typed parsing ideas for dates, decimals, enums, and section-aware object mapping.
- Reuse Flex client flow concept: request report, poll status, download payload.

### ibflex2
- Reuse maintained-parser compatibility patterns for modern IBKR Flex XML variations.
- Reuse strict parser constraints around report configuration (date formats and unsupported summary modes).
- Reuse the low-dependency parser + optional client split as a reference for adapter boundaries.

### flexquery
- Reuse small CLI ingestion flow and retries for polling report readiness.
- Reuse plugin-style separation: downloader core plus optional post-processing modules.
- Reuse output format detection concept for saved artifacts.

### IB_Flex
- Reuse the practical pipeline mindset: download -> persist -> analyze.
- Reuse idea of keeping raw storage for replay and auditability.

### finx-reports-ib
- Reuse Flex configuration checklist (required sections and settings) as validation baseline.
- Reuse idea of strict setup docs for reliable ingestion operations.

### ngv_reports_ibkr
- Reuse robust operational patterns around documentation, CI checks, and typed report-processing boundaries.
- Reuse Flex configuration guidance as a secondary cross-check for section-coverage validation.

### premium-tracker
- Reuse UI ideas only at high level (dashboard composition, drilldown narratives).
- Reuse privacy-first communication pattern for user trust.

## What to avoid in this project

- Avoid mixing parser, storage, analytics, and UI logic in one module.
- Avoid direct database calls outside the dedicated db layer.
- Avoid SQLite patterns from references for production path, because this project is PostgreSQL-first.
- Avoid broad scope from options-heavy analytics in MVP; keep stocks-first boundaries.
- Avoid direct code copy from references; implement native modules aligned with local architecture.

## License notes before borrowing code

- ibflex: MIT (per LICENSE.txt)
- ibflex2: MIT (per LICENSE.txt)
- flexquery: MIT (per LICENSE)
- IB_Flex: BSD-3-Clause (per LICENSE)
- finx-reports-ib: MIT (per LICENSE)
- ngv_reports_ibkr: BSD-3-Clause (per LICENSE)

Rule: if any snippet is adapted, preserve required attribution and keep adaptation minimal and traceable.

## Additional candidates from second GitHub scan (2026-02-14)

### Strong additions

- oittaa/ibkr-report-parser
	- Why useful: Mature Python project with packaging, CI, Docker, and API-level parsing patterns.
	- Reuse ideas: input normalization, report object model, production-ready packaging/testing setup.
	- Caveat: Tax-country-specific business logic; do not import domain assumptions into core ledger.

- romamo/py-ibkr
	- Why useful: Pydantic-first, modern typed parser approach for Flex XML.
	- Reuse ideas: strict model validation boundaries, tolerant parsing for messy IBKR enum/date variants.
	- Caveat: New/low-adoption repository; treat as design inspiration and verify behavior with fixtures.

- clifton/ib-flex (Rust)
	- Why useful: Excellent section coverage and reliability test design.
	- Reuse ideas: parser edge-case checklist, fixture strategy, API polling flow, type taxonomy.
	- Caveat: Different language/runtime; borrow architecture and tests, not implementation.

### Lower priority (optional)

- tfiala/ibkr-flex-statement-rs
	- Why useful: Good Flex section configuration matrix and field-level completeness checklist.
	- Reuse ideas: query configuration checklist to validate required report sections.
	- Caveat: Rust crate and parser shape differ from Python app architecture.

### Not recommended for now

- Narrow tax-only scripts and one-off analyzers with minimal stars/tests should not be used as primary references.
