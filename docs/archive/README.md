# Historical documentation archive

These records preserve the original scope, reasoning, contracts, and implementation
checklists. They are not current instructions or an active backlog. See the
[documentation index](../README.md) for maintained guides.

Archived document bodies are preserved, including old instructions, incomplete checkboxes,
path names, and claims that were true only when written. Inline paths in the original
root-level plans refer to the repository root; use this map for renamed documents.

| Original path | Preserved record or current destination |
| --- | --- |
| `MVP.md` | [Original implementation plan](MVP.md) |
| `initial_plan.md` | [Initial product design](initial_plan.md) |
| `implementation_task_list.md` | [Original outcome-ordered task checklist](implementation_task_list.md) |
| `tasks.md` | [Task 7 execution record, summaries, and user decisions](tasks.md) |
| `MVP_spec_freeze.md` | [Pre-cleanup specification snapshot](MVP_spec_freeze.md); maintained [contracts](../contracts.md) |
| `docs/task2_schema_contract.md` | [Initial migration schema contract](task2_schema_contract.md) |
| `max_plan.md` | [Long-term architecture reference](../design/long_term_architecture.md) |
| `.github/copilot-instructions.md` SQLite section | [Superseded SQLite/NFS guidance](sqlite_guidance.md) |

`MVP_evaluation.md` was empty and was removed on 2026-09-12. References to evaluation
outcomes in the engineering log are preserved; the nonempty freeze-sheet snapshot retains
the resulting decisions. No evaluation text existed in that file at cleanup time.

The original plans include superseded host-PostgreSQL, cron, authentication, corporate-action,
and scope assumptions. Current deployment uses Compose PostgreSQL and supplied systemd
timers; supported behavior and access requirements are described in the project README.
The baseline schema is not the full current database: later Alembic revisions remain
necessary. See [migration guidance](../migrations.md).
