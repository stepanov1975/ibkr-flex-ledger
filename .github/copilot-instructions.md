## Project requirements
1. Always read README.md at the start of the session to load architectural decisions, conventions, and patterns.
2. Use Tree of Thoughts (ToT) for implementation of new features or complex bug fixes.
3. When bug discovered/need to be fixed, if this bug can be reproduced using test, **first** create test thatreproduces bug, run test see it fails, fix bug, run test see it pass.
4. Code under `references/` is reference material only. It is not part of this application runtime. Reuse ideas and patterns only; implement project-native code in the main application modules.

### No Backward Compatibility Code Needed
- **Single-site application** with coordinated deployments
- Code and database update together in a single deployment
- **DO NOT write:**
  - Backward-compatible API paths
  - Support for old API versions
  - Feature flags or gradual rollouts
  - Field aliases for renamed columns
  - Deprecation periods or fallback code
- **DO:** Update all usages (handlers, templates, JS) in the same commit when changing APIs/models


## GENERAL HYGIENE & REUSE PROTOCOL
1. **Mandatory Reuse Audit:**
   * **Constraint:** Before writing ANY new helper function or class, you MUST perform a semantic search of the codebase.
   * **Action:** If similar logic exists (even if imperfect), refactor and extend the existing code rather than duplicating it. **DRY (Don't Repeat Yourself) is the primary law.**
2. **Live Documentation:**
   * Docstrings must reflect the *current* reality only.
   * **Prohibited:** "Change logs," dates, or "Before/After" notes in docstrings. If you change logic, overwrite the old docstring entirely.
3. **Formatting & Symbols:**
   * Use ASCII characters only in code and comments. **Strictly No Emojis** or non-standard symbols in code/comments.
   * Non-ASCII symbols are allowed in UI-facing HTML/Jinja content and Markdown files.
4. **Completion Gate:**
   * The task is NOT complete until you run the linting commands (`pylint`, `ruff`) and resolve all new errors. Do not present code as "finished" if it has red squiggles.
5. **Dates**: Use `YYYY-MM-DD` format in all comments, docstrings, and documentation. Use today's date; do not hallucinate past/future dates.

## NAMING & DISCOVERABILITY
1. **Discoverability Rule:** Name things based on *what* they affect, then *what* they do.
   * **Good:** `inventory_calculate_total()`, `inventory_fetch_items()` (Groups in autocomplete).
   * **Bad:** `calculate_total()`, `get_items()` (Scattered in autocomplete).
2. **Clarity Over Brevity:**
   * Names must be fully descriptive keywords. Code is read more often than written.
   * **Example:** `calculate_net_present_value` > `calc_npv`.
3. **Consistency:**
   * Use the same verb for the same concept across the project (e.g., do not mix `fetch`, `get`, and `retrieve`; pick one and stick to it).

## CODE STRUCTURE & OBJECT-ORIENTED DESIGN
1. **Encapsulation Rule (Class vs. Function):**
   * **When to use a Class:** If 3 or more functions share the same parameters (e.g., `config`, `logger`, `state`) or operate on the same data, they MUST be refactored into a class.
   * **When to use a Top-Level Function:** Only for "Pure Functions" (input → output, no side effects) or simple entry points.
2. **State Management:**
   * **Prohibited:** Global variables or mutable default arguments.
   * **Required:** Use `__init__` to inject dependencies and setup initial state.
3. **Data Structures:**
   * Use `@dataclass` for objects that primarily hold data (avoid raw dictionaries or tuples for complex data).
   * Use `Enum` for status codes or fixed options.
4. **Abstraction Layers:**
   * **Public Interface:** Public methods (`do_work()`) should read like a story.
   * **Private Implementation:** Complex logic must be hidden in private methods (`_calculate_metrics()`).

## TECHNICAL STANDARDS & LINTING
1. **Code Style (PEP 8):**
   * Adhere to PEP 8 naming conventions (CapWords for classes, snake_case for functions/vars).
   * **Line Length:** Limit to **120 characters** (aligns with Pylint config).
2. **Modern Python Idioms:**
   * Use `pathlib` instead of `os.path`.
   * Use `f-strings` for string interpolation (except in logging).
   * Use `dataclasses` or `Pydantic` for data structures instead of raw dictionaries.
3. **Architecture:**
   * **Entry Point:** Must use `if __name__ == "__main__":` block.
   * **SRP:** Functions must handle a single responsibility. Split large functions (>50 lines) into helpers.



## DOCUMENTATION STANDARDS
1. **Module-Level:** Every file must start with a docstring explaining its role and scope.
2. **Function/Class Docstrings (Google Style):**
   * **Context:** Explain *what* it does and *why* (business logic), not just how.
   * **Arguments:** Define parameters clearly. *Note: Ensure types in docstrings match type hints.*
   * **Returns:** Describe the output object and what `None` signifies if applicable.
   * **Raises:** **MANDATORY.** List all exceptions raised so callers know what errors to handle.
3. **Inline Comments:**
   * **The "Why" Rule:** Comments must explain the *intent/reasoning*, not the syntax.
   * **Bad:** `# increment i` (Redundant).
   * **Good:** `# increment retry counter to prevent infinite loop`.
4. **Maintenance:** If you modify logic, you **must** update the corresponding docstring immediately. Mismatched docs are worse than no docs.

## CODE QUALITY & ROBUSTNESS
1. **Error Handling:**
   * **Prohibited:** Broad `except Exception:` without re-raising. Capture specific errors only.
   * **Protocol:** On fatal errors, ensure resource cleanup (context managers) and exit with non-zero status codes.
   * **Logging:** Use standard `logging`. **Rule:** Use lazy formatting (`logger.info("Val: %s", val)`), NEVER f-strings in logger calls.
2. **Typing & Safety:**
   * **Strict Typing:** Apply `typing` hints to ALL function arguments and return values.
   * **Validation:** Validate inputs at the **system boundary** (CLI args, API payloads) using Pydantic or explicit checks. Do not clutter internal private methods with redundant assertions.
3. **Resource Management:**
   * Mandatory use of `with` statements (context managers) for file I/O, locks, and network connections.

## CLI & INTERFACE STANDARDS
1. **Argparse:** When using `argparse`, every argument must have:
   * A `help` description.
   * A specific `type` (e.g., `type=int` or `type=pathlib.Path`).
2. **Testability:** Design functions as "pure" logic separators (decoupled from I/O) to allow easy unit testing without heavy mocking.

## ROOT CAUSE ANALYSIS & FIXING
1. **Trace Before Coding:** Map data and control flow upstream to find the origin of the error.
   * **Constraint:** **NO BAND-AIDS.** Explicitly forbidden to add null-checks, try/except blocks, or type coercions without identifying *why* the state was invalid.
2. **Scope of Fix:** Fix the definition, not the usage.
   * If a shared utility is broken, fix the utility; do not hack the caller to handle the bug.
   * If the bug stems from structural weakness (tight coupling, bad assumptions), refactor the design rather than adding guards.
3. **Deliverable:** The fix must include the logic correction, technical debt cleanup, and a targeted regression test.

## TESTING STANDARDS
Important! When creating or running tests **always read `testing.md` for detailed testing protocols.**
**Code to support legacy functionality should never be added to project only to make old tests pass after project code changes made**
**Never** create tests for code in `references/` directory

## LINTING 
1. **Pre-Completion Gate:** Code is NOT complete until linting passes with zero errors.
2. **Always** read `linting.md` for detailed JavaScript and Python linting protocols.
3. **Never** run linting on code in `references/` directory

## Stale File Handling
When you encounter issues that seem to persist despite your fixes, consider the possibility of stale files.
In this case, halt implementation of the current task and ask from user to restart code editor.

# Persistent Memory Instructions (ai_memory.md)

Treat `ai_memory.md` as your long-term project memory.
1. **Initialization**: Always read `ai_memory.md` at the start of a session to load architectural decisions, conventions, and patterns.
2. **Updates**: After significant changes (bug fixes, new patterns, API choices), update the file. Add new insights or prune obsolete ones.
3. **Criteria**: Store only *durable* engineering knowledge (Why a decision was made, how a complex module works). Avoid chatter.
4. **Conflict Resolution**: If `ai_memory.md` conflicts with the actual codebase, trust the code and update the memory file.
5. **Format**: `- [YYYY-MM-DD] {TAG} :: Concise description`.
   - **Tags**: `DECISION` (Architecture/Libs), `PATTERN` (Reusable code), `FIX` (Solutions to tricky bugs), `TODO`, `GLOSSARY`.


# Copilot: Python "Future-Self Notes" (FSN) Protocol

**Mission:** Prevent regression loops. If code looks "wrong" or "inefficient" but has an FSN, **assume it is intentional**. Do not "fix" it back to the broken state.

## Rules of Engagement

1.  **Read First:** Before editing *any* block, scan for `FSN` tags.
2.  **Obey the Note:** If an FSN says "Do not use X," **do not suggest X**, even if it seems standard.
3.  **Create FSNs:** When fixing a bug caused by a misunderstanding of non-obvious logic:
    * Add an FSN explaining *why* the weird logic is necessary.
    * Add a **runtime guard** (ValueError/TypeError) to enforce it programmatically.
4.  **Preserve:** Move FSNs during refactors. Delete them *only* if the underlying constraint is removed.

## FSN Template
Use this format for non-obvious logic:

```python
# FSN[YYYY-MM-DD]: <DO NOT ... / ALWAYS ...> (Instruction for AI/Devs)
# Context: <Why the "obvious" fix breaks things> | Symptom: <What fails>
# Guard: <Runtime check> | Test: <pytest_name>

# SQLite Best Practices

## General Principles
- Prioritize using parameterized queries (e.g., `?` placeholders) to prevent SQL injection vulnerabilities. Never use f-strings or string formatting to insert values directly into SQL statements.
- Always manage database connections properly. In Python, use a `with` statement to ensure the connection is automatically closed.
- For multiple related database operations, wrap them in a transaction (`BEGIN TRANSACTION; ... COMMIT;`) to ensure atomicity.
- `sqlite3.connect()` calls MUST include NFS-safe settings: `timeout=30.0` parameter and execute these PRAGMAs: `busy_timeout=30000` (tolerate NFS lock latency), `synchronous=FULL` (max durability), `locking_mode=EXCLUSIVE` (single-host optimization), `mmap_size=0` (disable mmap on NFS), `wal_autocheckpoint=1000` (manage WAL growth).
- Production database resides on NFS mount with single-host access; all connections require these settings to prevent "disk I/O error" under concurrent access.

## Naming Conventions
- **Table Names:** Use plural nouns in `snake_case`. For example: `users`, `blog_posts`, `product_orders`.
- **Column Names:** Use singular nouns in `snake_case`. For example: `first_name`, `email_address`, `order_date`.
- **Primary Keys:**
    - Prefer a simple `id` for the primary key column.
    - Use `INTEGER PRIMARY KEY` to create an auto-incrementing alias for the `rowid`.
- **Foreign Keys:**
    - Name foreign key columns using the singular name of the referenced table followed by `_id`.
    - For a table named `users`, the foreign key in the `posts` table should be `user_id`.

## Schema and Data Types
- Use the most specific and appropriate data types available in SQLite: `INTEGER`, `TEXT`, `REAL`, `BLOB`.
- Define `NOT NULL` constraints for columns that must always have a value.
- Use `DEFAULT` constraints for columns that should have a default value if one isn't provided.
- Add `UNIQUE` constraints to columns that must not contain duplicate values, like usernames or email addresses.
- When creating tables, explicitly define foreign key constraints to enforce referential integrity. Ensure `PRAGMA foreign_keys = ON;` is executed for each connection.