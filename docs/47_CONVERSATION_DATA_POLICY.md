# Conversation Data Policy

Phase 0 defaults are privacy-preserving foundations; deployment-specific durations must be
approved before persistent conversation storage is enabled.

- **Retention:** threads, messages, attachments, and user-visible events are retained only as
  long as needed for the user-facing service. Internal traces use a shorter operational window
  and must contain redacted payloads. No indefinite retention is the default.
- **Deletion:** deleting a thread soft-deletes it immediately from user access and queues its
  messages, attachments, derived memories, and trace links for permanent deletion. Legal holds
  must be explicit, authorized, and auditable.
- **Traces:** credentials and direct PII are redacted before logging or event delivery. Raw
  prompts, tool results, attachment content, and memory content are excluded from traces by
  default. `INTERNAL` and `ADMIN` events are never exposed as ordinary user events.
- **Memory consent:** memory starts as `PENDING_CONSENT`. It cannot become `ACTIVE` without an
  affirmative consent timestamp. Consent is revocable; deletion or revocation prevents future
  retrieval and schedules stored content for deletion.
- **Ownership:** access is workspace-scoped and owner-scoped. Administrative access is explicit;
  internal access does not imply that content may be shown to end users.
