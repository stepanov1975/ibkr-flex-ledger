# Documentation index

IBKR Flex Ledger has progressed beyond its initial MVP. Maintained guides describe the
current application; historical plans preserve rationale and acceptance criteria without
restricting new work to the original delivery scope.

## Current guides

| Document | Purpose |
| --- | --- |
| [Project README](../README.md) | Features, supported scope, configuration, and deployment |
| [Technical reference](technical_reference.md) | Architecture, accounting behavior, APIs, jobs, and release gates |
| [Contracts](contracts.md) | Natural keys, valuation rules, tolerances, CSV schemas, API conventions, and reliability targets |
| [Architecture conventions](architecture_conventions.md) | Mandatory module, database, and reference-code boundaries |
| [Flex query field catalog](flex_query_fields.md) | Reference-derived field descriptions for core report sections |
| [Migrations](migrations.md) | Current Alembic workflow and deployment considerations |
| [Operations](operations.md) | Import troubleshooting, replay repair, alerts, retention, backup, and recovery |
| [Systemd setup](../deploy/systemd/README.md) | Timer installation and optional outbound-alert scheduling |
| [Testing policy](../testing.md) | Regression-test integrity and database-dependent verification |
| [Linting and type checking](../linting.md) | Ruff and MyPy commands and suppression policy |
| [Contributor instructions](../AGENTS.md) | Change scope, simplicity, and verification rules |
| [Copilot instructions](../.github/copilot-instructions.md) | Additional contributor conventions and engineering-note format |
| [Reference repository notes](../references/REFERENCE_NOTES.md) | External study material, reuse boundaries, and license notes |

Commands and inline code paths in technical guides are relative to the repository root
unless stated otherwise. Markdown links are relative to their containing document.
The field catalog describes upstream fields, not a guarantee that every field is consumed.
Live API request/response schemas are available at `/docs` on the running application.

## Design and evidence

- [Early plans and original contracts](archive/README.md): archived MVP scope, initial
  schema, completed task records, and superseded contributor advice.
- [Long-term architecture](design/long_term_architecture.md): preserved end-state ideas,
  proposed modules, and phase breakdowns. Some features have since shipped; this is not a
  current feature inventory or a committed roadmap.
- [Feature designs and implementation plans](superpowers/README.md): dated proposals,
  execution details, and supersession notes.
- [Code review records](reviews/README.md): findings and their recorded fixes.
- [Release evidence](releases/README.md): dated validation and recovery measurements.
- [Engineering decision log](../ai_memory.md): chronological decisions and fixes, including
  superseded approaches.
- [Agent implementation reports](../.superpowers/sdd): historical task-level evidence.

## Keeping documentation current

Update the guide that owns the information when behavior changes. Keep user-facing
features and deployment in the project README, implementation details in the technical
reference, numerical/API contracts in the contracts document, and operational commands in
the operations and migration guides. Link to the owning guide instead of duplicating it.

Label future proposals, unimplemented policy targets, and historical evidence explicitly.
Preserve historical decisions and validation results with their original dates; they do
not prove the current checkout passes release gates. Do not rewrite vendored reference
repositories as application documentation.

`MVP_spec_freeze.md` remains a short reference entry because reconciliation API metadata
still emits `MVP_spec_freeze.md#4`. Its tolerance matrix now lives in the contracts guide.
