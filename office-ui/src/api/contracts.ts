export type ThreadStatus = "ACTIVE" | "ARCHIVED" | "DELETED";
export type MessageRole = "USER" | "ASSISTANT" | "SYSTEM" | "TOOL";
export type MessagePartType =
  | "TEXT" | "TABLE" | "CODE" | "SOURCE" | "TOOL_CALL"
  | "ARTIFACT" | "CHART" | "AGENT_STATUS" | "APPROVAL" | "WARNING";

export interface Thread {
  id: string;
  workspace_id: string;
  owner_user_id: string;
  project_id: string | null;
  title: string | null;
  status: ThreadStatus;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MessagePart {
  type: MessagePartType;
  content: string | Record<string, unknown> | unknown[];
  metadata?: Record<string, unknown>;
}

export interface ArtifactProvenance {
  evidence_ids: string[];
  calculation_version?: string | null;
  query_version?: string | null;
  prompt_version?: string | null;
  tool_version?: string | null;
  model_version?: string | null;
  source_metadata: Record<string, unknown>;
}

export interface Artifact {
  id: string;
  artifact_type: string;
  classification: "ui" | "factual" | "derived";
  evidence_ids: string[];
  provenance: ArtifactProvenance;
  payload: Record<string, unknown>;
}

export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  checksum_sha256: string | null;
  status: "PENDING" | "READY" | "FAILED" | "DELETED";
}

export interface InitiateAttachmentResponse {
  attachment: Attachment;
  upload: { method: "PUT"; url: string };
}

export interface Message {
  id: string;
  thread_id: string;
  workspace_id: string;
  user_id: string | null;
  role: MessageRole;
  parts: MessagePart[];
  run_id: string | null;
  parent_message_id: string | null;
  created_at: string;
  /** Set when the run writes the final answer; minus created_at = response time. */
  updated_at?: string;
}

export interface SubmitMessageRequest {
  parts: MessagePart[];
  parent_message_id?: string | null;
  scope?: Record<string, unknown>;
  execution_mode?: "development" | "staging" | "production";
}

export interface SubmitMessageResponse {
  message_id: string;
  run_id: string;
  mission_id: string;
}

export interface ActivityEvent {
  id: string;
  thread_id: string;
  workspace_id: string;
  run_id: string | null;
  sequence: number;
  event_type: string;
  actor_type: string | null;
  actor_id: string | null;
  title: string | null;
  summary: string | null;
  evidence_ids: string[];
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  created_at: string;
  type?: string;
}

export interface CreateThreadRequest {
  title?: string | null;
  project_id?: string | null;
  metadata?: Record<string, unknown>;
}

export type MemoryScope = "USER" | "PROJECT" | "THREAD" | "EPISODIC";
export type MemoryType =
  | "FACT" | "DECISION" | "PREFERENCE" | "OUTCOME"
  | "CONSTRAINT" | "DEFINITION" | "SUMMARY" | "INSTRUCTION";
export type MemoryStatus =
  | "CANDIDATE" | "ACTIVE" | "SUPERSEDED" | "ARCHIVED"
  | "DELETED" | "PENDING_CONSENT";

export interface MemoryItem {
  id: string;
  workspace_id: string;
  owner_user_id: string;
  project_id: string | null;
  thread_id: string | null;
  scope: MemoryScope;
  type: MemoryType;
  status: MemoryStatus;
  content: string | Record<string, unknown>;
  normalized_content: string;
  provenance: Record<string, unknown>;
  confidence: number;
  salience: number;
  source_run_id: string | null;
  source_message_ids: string[];
  source_evidence_ids: string[];
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoryPreference {
  workspace_id: string;
  owner_user_id: string;
  opted_out: boolean;
  updated_at: string;
}
