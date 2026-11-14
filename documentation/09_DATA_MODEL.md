# 10. Data model (generated)

Generated from `migrations/001…018` on 2026-11-11 by applying the `CREATE TABLE` statements in order (later `ALTER TABLE ... ADD COLUMN` statements are added to the table). Types are as written in the migrations.

33 tables.

| Table | Columns | Defined in |
|---|---|---|
| `missions` | 16 | `001_init.sql` |
| `mission_events` | 8 | `001_init.sql` |
| `evidence_artifacts` | 9 | `001_init.sql` |
| `claims` | 9 | `001_init.sql` |
| `leadership_transfers` | 8 | `001_init.sql` |
| `threads` | 12 | `003_conversation_foundation.sql` |
| `thread_participants` | 5 | `003_conversation_foundation.sql` |
| `runs` | 17 | `003_conversation_foundation.sql` |
| `messages` | 9 | `003_conversation_foundation.sql` |
| `message_parts` | 7 | `003_conversation_foundation.sql` |
| `run_attempts` | 13 | `003_conversation_foundation.sql` |
| `run_events` | 23 | `003_conversation_foundation.sql` |
| `attachments` | 13 | `003_conversation_foundation.sql` |
| `artifacts` | 14 | `003_conversation_foundation.sql` |
| `thread_summaries` | 12 | `003_conversation_foundation.sql` |
| `memories` | 34 | `006_memory_and_thread_summaries.sql` |
| `memory_preferences` | 4 | `006_memory_and_thread_summaries.sql` |
| `run_memory_usage` | 6 | `006_memory_and_thread_summaries.sql` |
| `approval_requests` | 13 | `007_search_observability_hitl.sql` |
| `approval_decision_events` | 8 | `007_search_observability_hitl.sql` |
| `approval_rollbacks` | 6 | `007_search_observability_hitl.sql` |
| `telemetry_loss` | 6 | `007_search_observability_hitl.sql` |
| `submission_outbox` | 4 | `011_transactional_persistence.sql` |
| `episodic_events` | 14 | `012_episodic_events.sql` |
| `v3_artifacts` | 7 | `013_v3_artifacts.sql` |
| `message_feedback` | 10 | `014_message_feedback.sql` |
| `monitor.metrics` | 13 | `016_monitor_schema.sql` |
| `monitor.target_versions` | 12 | `016_monitor_schema.sql` |
| `monitor.evaluations` | 17 | `016_monitor_schema.sql` |
| `monitor.findings` | 17 | `016_monitor_schema.sql` |
| `monitor.finding_events` | 6 | `016_monitor_schema.sql` |
| `monitor.labels` | 6 | `016_monitor_schema.sql` |
| `monitor.known_periods` | 8 | `018_monitor_known_periods.sql` |

### `missions`

| Column | Definition |
|---|---|
| `mission_id` | TEXT PRIMARY KEY |
| `user_query` | TEXT NOT NULL |
| `normalized_query` | TEXT |
| `status` | TEXT NOT NULL |
| `mission_lead` | TEXT |
| `active_specialist` | TEXT |
| `leadership_epoch` | INTEGER NOT NULL DEFAULT 0 |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `route` | TEXT |
| `result_json` | JSONB |
| `raw_json` | JSONB |
| `workspace_id` | TEXT |
| `owner_user_id` | TEXT |
| `thread_id` | TEXT |
| `run_id` | TEXT |

### `mission_events`

| Column | Definition |
|---|---|
| `event_id` | BIGSERIAL PRIMARY KEY |
| `mission_id` | TEXT NOT NULL REFERENCES missions(mission_id) |
| `task_id` | TEXT |
| `agent_id` | TEXT |
| `event_type` | TEXT NOT NULL |
| `payload` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `source_seq` | BIGINT |

### `evidence_artifacts`

| Column | Definition |
|---|---|
| `evidence_id` | TEXT PRIMARY KEY |
| `mission_id` | TEXT NOT NULL REFERENCES missions(mission_id) |
| `source` | TEXT NOT NULL |
| `metric_or_fact` | TEXT NOT NULL |
| `value_json` | JSONB NOT NULL |
| `dimensions` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `provenance` | JSONB NOT NULL |
| `quality_flags` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `retrieved_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `claims`

| Column | Definition |
|---|---|
| `claim_id` | TEXT PRIMARY KEY |
| `mission_id` | TEXT NOT NULL REFERENCES missions(mission_id) |
| `claim_type` | TEXT NOT NULL |
| `text` | TEXT NOT NULL |
| `support_refs` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `contradiction_refs` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `trust_label` | TEXT NOT NULL |
| `gate_status` | TEXT NOT NULL DEFAULT 'pending |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `leadership_transfers`

| Column | Definition |
|---|---|
| `transfer_id` | BIGSERIAL PRIMARY KEY |
| `mission_id` | TEXT NOT NULL REFERENCES missions(mission_id) |
| `epoch` | INTEGER NOT NULL |
| `from_agent` | TEXT NOT NULL |
| `to_agent` | TEXT NOT NULL |
| `reason` | TEXT NOT NULL |
| `evidence_refs` | JSONB NOT NULL |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `threads`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `project_id` | TEXT |
| `title` | TEXT |
| `status` | TEXT NOT NULL DEFAULT 'ACTIVE |
| `metadata` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `search_vector` | tsvector GENERATED ALWAYS AS ( to_tsvector('simple', coalesce(title, '') // ' ' // coalesce(metadata::text, '')) ) STORED |
| `embedding` | vector(1536) |
| `deleted_at` | TIMESTAMPTZ |

### `thread_participants`

| Column | Definition |
|---|---|
| `thread_id` | TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `user_id` | TEXT NOT NULL |
| `role` | TEXT NOT NULL DEFAULT 'member |
| `joined_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

Constraints: `PRIMARY KEY (thread_id, user_id)`


### `runs`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `thread_id` | TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `requested_by_user_id` | TEXT NOT NULL |
| `mission_id` | TEXT REFERENCES missions(mission_id) ON DELETE SET NULL |
| `status` | TEXT NOT NULL DEFAULT 'QUEUED |
| `current_attempt` | INTEGER NOT NULL DEFAULT 0 CHECK (current_attempt >= 0) |
| `metadata` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `started_at` | TIMESTAMPTZ |
| `completed_at` | TIMESTAMPTZ |
| `max_attempts` | INTEGER NOT NULL DEFAULT 3 |
| `retry_count` | INTEGER NOT NULL DEFAULT 0 |
| `next_retry_at` | TIMESTAMPTZ |
| `cancel_requested_at` | TIMESTAMPTZ |
| `search_vector` | tsvector GENERATED ALWAYS AS ( to_tsvector('simple', coalesce(status, '') // ' ' // coalesce(metadata::text, '')) ) STORED |
| `embedding` | vector(1536) |

### `messages`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `thread_id` | TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `user_id` | TEXT |
| `role` | TEXT NOT NULL |
| `run_id` | TEXT REFERENCES runs(id) ON DELETE SET NULL |
| `parent_message_id` | TEXT REFERENCES messages(id) ON DELETE SET NULL |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `message_parts`

| Column | Definition |
|---|---|
| `message_id` | TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE |
| `position` | INTEGER NOT NULL CHECK (position >= 0) |
| `part_type` | TEXT NOT NULL |
| `content` | JSONB NOT NULL |
| `metadata` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `search_vector` | tsvector GENERATED ALWAYS AS ( to_tsvector('simple', coalesce(content::text, '') // ' ' // coalesce(metadata::text, '')) ) STORED |
| `embedding` | vector(1536) |

Constraints: `PRIMARY KEY (message_id, position)`


### `run_attempts`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `run_id` | TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE |
| `attempt_number` | INTEGER NOT NULL CHECK (attempt_number >= 1) |
| `status` | TEXT NOT NULL DEFAULT 'RUNNING |
| `error_code` | TEXT |
| `error_message` | TEXT |
| `started_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `completed_at` | TIMESTAMPTZ |
| `worker_id` | TEXT |
| `lease_expires_at` | TIMESTAMPTZ |
| `heartbeat_at` | TIMESTAMPTZ |
| `retryable` | BOOLEAN NOT NULL DEFAULT TRUE |
| `version` | INTEGER NOT NULL DEFAULT 0 |

Constraints: `UNIQUE (run_id, attempt_number)`


### `run_events`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `thread_id` | TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `run_id` | TEXT REFERENCES runs(id) ON DELETE CASCADE |
| `owner_user_id` | TEXT |
| `actor_user_id` | TEXT |
| `sequence` | BIGINT NOT NULL |
| `event_type` | TEXT NOT NULL |
| `visibility` | TEXT NOT NULL DEFAULT 'USER |
| `payload` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `event_json` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `thread_sequence` | BIGINT |
| `actor_type` | TEXT |
| `actor_id` | TEXT |
| `title` | TEXT |
| `summary` | TEXT |
| `parent_event_id` | TEXT |
| `evidence_ids` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `metadata` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `started_at` | TIMESTAMPTZ |
| `completed_at` | TIMESTAMPTZ |
| `duration_ms` | BIGINT |

Constraints: `UNIQUE (run_id, sequence)`


### `attachments`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `thread_id` | TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `message_id` | TEXT REFERENCES messages(id) ON DELETE SET NULL |
| `filename` | TEXT NOT NULL |
| `content_type` | TEXT NOT NULL |
| `size_bytes` | BIGINT NOT NULL CHECK (size_bytes >= 0) |
| `storage_uri` | TEXT |
| `status` | TEXT NOT NULL DEFAULT 'PENDING |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `scan_status` | TEXT NOT NULL DEFAULT 'PENDING |
| `scan_detail` | TEXT NOT NULL DEFAULT |

Constraints: `checksum_sha256 TEXT`


### `artifacts`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `artifact_type` | TEXT NOT NULL |
| `payload` | JSONB NOT NULL |
| `mission_id` | TEXT REFERENCES missions(mission_id) ON DELETE SET NULL |
| `thread_id` | TEXT REFERENCES threads(id) ON DELETE SET NULL |
| `run_id` | TEXT REFERENCES runs(id) ON DELETE SET NULL |
| `message_id` | TEXT REFERENCES messages(id) ON DELETE SET NULL |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `classification` | TEXT NOT NULL DEFAULT 'ui |
| `evidence_ids` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `provenance` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `search_vector` | tsvector GENERATED ALWAYS AS ( to_tsvector('simple', coalesce(artifact_type, '') // ' ' // coalesce(payload::text, '')) ) STORED |
| `embedding` | vector(1536) |

### `thread_summaries`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `thread_id` | TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `through_message_id` | TEXT REFERENCES messages(id) ON DELETE SET NULL |
| `summary` | TEXT NOT NULL |
| `metadata` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `owner_user_id` | TEXT |
| `covered_message_ids` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `source_evidence_ids` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `mode` | TEXT NOT NULL DEFAULT 'extractive |
| `version` | TEXT NOT NULL DEFAULT 'phase6-v1 |

### `memories`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `project_id` | TEXT |
| `thread_id` | TEXT REFERENCES threads(id) ON DELETE SET NULL |
| `scope` | TEXT NOT NULL CHECK (scope IN ('USER', 'PROJECT', 'THREAD', 'EPISODIC')) |
| `type` | TEXT NOT NULL |
| `status` | TEXT NOT NULL DEFAULT 'PENDING_CONSENT |
| `content` | JSONB NOT NULL |
| `normalized_content` | TEXT NOT NULL |
| `structured_data` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `provenance` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `confidence` | DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1) |
| `salience` | DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (salience BETWEEN 0 AND 1) |
| `source_run_id` | TEXT REFERENCES runs(id) ON DELETE SET NULL |
| `source_message_id` | TEXT REFERENCES messages(id) ON DELETE SET NULL |
| `source_message_ids` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `source_evidence_ids` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `supersedes_id` | TEXT REFERENCES memories(id) ON DELETE SET NULL |
| `superseded_by_id` | TEXT REFERENCES memories(id) ON DELETE SET NULL |
| `consented_at` | TIMESTAMPTZ |
| `pinned` | BOOLEAN NOT NULL DEFAULT FALSE |
| `requires_confirmation` | BOOLEAN NOT NULL DEFAULT TRUE |
| `valid_from` | TIMESTAMPTZ |
| `valid_to` | TIMESTAMPTZ |
| `expires_at` | TIMESTAMPTZ |
| `last_used_at` | TIMESTAMPTZ |
| `archived_at` | TIMESTAMPTZ |
| `deleted_at` | TIMESTAMPTZ |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `embedding` | vector(1536) |
| `search_vector` | tsvector GENERATED ALWAYS AS ( to_tsvector('simple', coalesce(normalized_content, '') // ' ' // coalesce(content::text, '')) ) STORED |
| `embedding` | vector(1536) |

Constraints: `CHECK (scope <> 'THREAD' OR thread_id IS NOT NULL)`; `CHECK (scope NOT IN ('PROJECT', 'EPISODIC') OR project_id IS NOT NULL)`; `CHECK (status <> 'ACTIVE' OR consented_at IS NOT NULL)`; `CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)`


### `memory_preferences`

| Column | Definition |
|---|---|
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `opted_out` | BOOLEAN NOT NULL DEFAULT FALSE |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

Constraints: `PRIMARY KEY (workspace_id, owner_user_id)`


### `run_memory_usage`

| Column | Definition |
|---|---|
| `run_id` | TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE |
| `memory_id` | TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `provenance` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `used_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

Constraints: `PRIMARY KEY (run_id, memory_id)`


### `approval_requests`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `run_id` | TEXT REFERENCES runs(id) ON DELETE SET NULL |
| `action_type` | TEXT NOT NULL |
| `action_preview` | JSONB NOT NULL |
| `required_role` | TEXT NOT NULL |
| `status` | TEXT NOT NULL CHECK (status IN ( 'REQUESTED','APPROVED','REJECTED','EXPIRED','CANCELLED','EXECUTED','ROLLED_BACK' )) |
| `idempotency_key` | TEXT NOT NULL |
| `dry_run` | BOOLEAN NOT NULL DEFAULT TRUE |
| `expires_at` | TIMESTAMPTZ |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

Constraints: `checkpoint_resume_token TEXT`; `UNIQUE(workspace_id, owner_user_id, idempotency_key)`


### `approval_decision_events`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `approval_id` | TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE |
| `from_status` | TEXT |
| `to_status` | TEXT NOT NULL |
| `actor_principal_id` | TEXT NOT NULL |
| `reason` | TEXT |
| `metadata` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `approval_rollbacks`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `approval_id` | TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE |
| `actor_principal_id` | TEXT NOT NULL |
| `action` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `outcome` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `telemetry_loss`

| Column | Definition |
|---|---|
| `id` | BIGSERIAL PRIMARY KEY |
| `workspace_id` | TEXT |
| `component` | TEXT NOT NULL |
| `lost_count` | BIGINT NOT NULL DEFAULT 1 |
| `reason` | TEXT |
| `observed_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `submission_outbox`

| Column | Definition |
|---|---|
| `run_id` | TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `published_at` | TIMESTAMPTZ |
| `attempts` | INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0) |

### `episodic_events`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `project_id` | TEXT |
| `thread_id` | TEXT |
| `run_id` | TEXT |
| `event_type` | TEXT NOT NULL CHECK ( event_type IN ('DECISION', 'ATTEMPT', 'OUTCOME', 'ERROR', 'RETRY', 'ROLLBACK') ) |
| `summary` | TEXT NOT NULL |
| `entities` | JSONB NOT NULL DEFAULT '[]'::jsonb |
| `details` | JSONB NOT NULL DEFAULT '{}'::jsonb |
| `supersedes_id` | TEXT |
| `superseded_by_id` | TEXT |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `v3_artifacts`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `artifact_type` | TEXT NOT NULL |
| `mission_id` | TEXT |
| `thread_id` | TEXT |
| `body` | TEXT NOT NULL |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `message_feedback`

| Column | Definition |
|---|---|
| `id` | TEXT PRIMARY KEY |
| `workspace_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `thread_id` | TEXT NOT NULL |
| `message_id` | TEXT NOT NULL |
| `run_id` | TEXT |
| `rating` | TEXT NOT NULL CHECK (rating IN ('up', 'down')) |
| `note` | TEXT NOT NULL DEFAULT |
| `supersedes_id` | TEXT |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `monitor.metrics`

| Column | Definition |
|---|---|
| `metric_id` | TEXT PRIMARY KEY |
| `label` | TEXT NOT NULL |
| `unit` | TEXT NOT NULL DEFAULT |
| `direction_bad` | TEXT NOT NULL CHECK (direction_bad IN ('down', 'up', 'both', 'none')) |
| `eval_window` | TEXT NOT NULL CHECK (eval_window IN ('day', 'trailing_7d_pooled', 'hour_pace')) |
| `intraday` | BOOLEAN NOT NULL DEFAULT FALSE |
| `settles_after_days` | INTEGER NOT NULL DEFAULT 0 CHECK (settles_after_days >= 0) |
| `enabled` | BOOLEAN NOT NULL DEFAULT TRUE |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `kind` | TEXT NOT NULL DEFAULT 'catalogue' CHECK (kind IN ('catalogue', 'ratio')) |
| `numerator_id` | TEXT |
| `denominator_id` | TEXT |
| `weight_id` | TEXT |

### `monitor.target_versions`

| Column | Definition |
|---|---|
| `id` | BIGSERIAL PRIMARY KEY |
| `brand_id` | BIGINT NOT NULL |
| `metric_id` | TEXT NOT NULL REFERENCES monitor.metrics(metric_id) |
| `floor_value` | DOUBLE PRECISION |
| `target_value` | DOUBLE PRECISION |
| `ceiling_value` | DOUBLE PRECISION |
| `seed` | TEXT NOT NULL CHECK (seed IN ('history', 'owner')) |
| `seed_window_start` | DATE |
| `seed_window_end` | DATE |
| `note` | TEXT NOT NULL DEFAULT |
| `created_by` | TEXT NOT NULL |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

Constraints: `CHECK (floor_value IS NULL OR target_value IS NULL OR floor_value <= target_value)`; `CHECK (target_value IS NULL OR ceiling_value IS NULL OR target_value <= ceiling_value)`; `CHECK (floor_value IS NULL OR ceiling_value IS NULL OR floor_value <= ceiling_value)`


### `monitor.evaluations`

| Column | Definition |
|---|---|
| `id` | BIGSERIAL PRIMARY KEY |
| `run_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `brand_id` | BIGINT NOT NULL |
| `metric_id` | TEXT NOT NULL REFERENCES monitor.metrics(metric_id) |
| `grain` | TEXT NOT NULL CHECK (grain IN ('hour', 'day')) |
| `period_start` | TIMESTAMPTZ NOT NULL |
| `observed` | DOUBLE PRECISION |
| `expected` | DOUBLE PRECISION |
| `expected_lo` | DOUBLE PRECISION |
| `expected_hi` | DOUBLE PRECISION |
| `target_version_id` | BIGINT REFERENCES monitor.target_versions(id) |
| `status` | TEXT NOT NULL CHECK (status IN ( 'ok', 'below_floor', 'below_target', 'above_ceiling', 'above_target', 'untrusted', 'no_target', 'no_data')) |
| `p_beyond` | DOUBLE PRECISION CHECK (p_beyond IS NULL OR (p_beyond >= 0 AND p_beyond <= 1)) |
| `impact_low` | DOUBLE PRECISION |
| `impact_high` | DOUBLE PRECISION |
| `data_status` | TEXT NOT NULL CHECK (data_status IN ('fresh', 'stale', 'partial', 'provisional')) |
| `details` | JSONB NOT NULL DEFAULT '{}'::jsonb |

### `monitor.findings`

| Column | Definition |
|---|---|
| `finding_id` | TEXT PRIMARY KEY |
| `brand_id` | BIGINT NOT NULL |
| `metric_id` | TEXT REFERENCES monitor.metrics(metric_id) |
| `entity_type` | TEXT NOT NULL DEFAULT 'brand |
| `entity_id` | TEXT NOT NULL DEFAULT |
| `detector` | TEXT NOT NULL |
| `state` | TEXT NOT NULL CHECK (state IN ('open', 'ongoing', 'resolved')) |
| `severity` | TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')) |
| `confidence` | DOUBLE PRECISION CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)) |
| `impact_low` | DOUBLE PRECISION |
| `impact_high` | DOUBLE PRECISION |
| `summary` | TEXT NOT NULL |
| `diagnosis` | JSONB |
| `recommendation` | JSONB |
| `opened_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `resolved_at` | TIMESTAMPTZ |

### `monitor.finding_events`

| Column | Definition |
|---|---|
| `id` | BIGSERIAL PRIMARY KEY |
| `finding_id` | TEXT NOT NULL REFERENCES monitor.findings(finding_id) ON DELETE CASCADE |
| `at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| `from_state` | TEXT |
| `to_state` | TEXT NOT NULL |
| `note` | TEXT NOT NULL DEFAULT |

### `monitor.labels`

| Column | Definition |
|---|---|
| `id` | BIGSERIAL PRIMARY KEY |
| `finding_id` | TEXT NOT NULL REFERENCES monitor.findings(finding_id) ON DELETE CASCADE |
| `label` | TEXT NOT NULL CHECK (label IN ('useful', 'noise', 'wrong_action')) |
| `note` | TEXT NOT NULL DEFAULT |
| `labelled_by` | TEXT NOT NULL |
| `labelled_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

### `monitor.known_periods`

| Column | Definition |
|---|---|
| `id` | BIGSERIAL PRIMARY KEY |
| `brand_id` | BIGINT NOT NULL |
| `start_day` | DATE NOT NULL |
| `end_day` | DATE NOT NULL |
| `kind` | TEXT NOT NULL CHECK (kind IN ('ads_paused', 'sale', 'outage', 'other')) |
| `reason` | TEXT NOT NULL |
| `created_by` | TEXT NOT NULL |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() |

Constraints: `CHECK (end_day >= start_day)`
