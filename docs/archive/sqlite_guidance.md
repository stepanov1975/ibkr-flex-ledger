> Historical record. Preserved for design rationale and implementation evidence;
> it does not define current behavior or an active work queue. See the
> [documentation index](../README.md).
> Obsolete imported contributor guidance. This application uses PostgreSQL, not SQLite or the NFS deployment described below.

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
