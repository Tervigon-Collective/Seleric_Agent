---
name: database-migration
description: Safe Alembic/PostgreSQL schema migration workflow for Seleric Voice Node services, respecting the current/history split, RLS, and retention rules in doc 14. Use whenever a ticket adds or changes a table/column/index.
---

# Database migration workflow

1. Check `14_DATA_MODEL_AND_PERSISTENCE.md` for the schema this table
   belongs to — confirm current/history split, retention policy, and RLS
   requirements before writing the migration, not after.
2. Write the Alembic migration with an explicit downgrade path — never a
   one-way migration unless truly irreversible (and say why in the
   migration docstring if so).
3. For anything adding/removing a NOT NULL constraint or column on a table
   likely to have existing rows: consider lock duration and whether a
   backfill step is needed. State the assumption if the table is currently
   empty (pre-launch) vs. must handle live data.
4. Apply RLS policies from doc 14 in the same migration that creates the
   table, not a follow-up.
5. Run the migration up and down against a local PostgreSQL 16 instance
   before considering it done.
6. Record the migration in the ticket's Completion Evidence with the
   command actually run and its output.
