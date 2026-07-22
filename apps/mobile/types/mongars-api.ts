export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type ReadinessResponse = {
  status: 'ready' | 'not_ready';
  dependencies: {
    database: {
      healthy: boolean;
      error?: string;
    };
    inference: {
      backend: string;
      healthy: boolean;
      backend_reachable: boolean;
      chat_model_ready: boolean;
      embedding_model_ready: boolean;
      latency_ms: number;
      error_code: string | null;
    };
    web_search?: {
      enabled: boolean;
      healthy: boolean;
      latency_ms: number;
      error_code: string | null;
    };
    worker?: {
      healthy: boolean;
      status: string;
      component_id: string | null;
      instance_id: string | null;
      version: string | null;
      git_sha: string | null;
      last_seen_at: string | null;
      age_seconds: number | null;
      error_code: string | null;
    };
    parser?: {
      healthy: boolean;
      version: string | null;
      error_code: string | null;
    };
    embedding_space?: {
      healthy: boolean;
      status: string;
      space_id: string | null;
      model_alias: string | null;
      model_digest: string | null;
      dimension: number | null;
      worker_space_id: string | null;
      total_chunk_count: number;
      compatible_chunk_count: number;
      legacy_chunk_count: number;
      reindex_required: boolean;
      error_code: string | null;
    };
  };
};

export type ChatRequest = {
  message: string;
  session_id?: string | null;
  require_local_only?: boolean;
  web_search?: 'off' | 'auto' | 'required';
};

export type WebSource = {
  title: string;
  url: string;
};

export type ChatResponse = {
  trace_id: string;
  session_id: string;
  status: 'ok';
  answer: string;
  model: string;
  memory_hits: number;
  web_search_status:
    | 'not_requested'
    | 'ok'
    | 'disabled'
    | 'unavailable'
    | 'no_results'
    | 'context_limited';
  sources?: WebSource[];
};

export type MemorySearchRequest = {
  query: string;
  top_k?: number;
  mode?: 'semantic' | 'hybrid';
};

export type MemorySearchHit = {
  chunk_id: string;
  document_id: string;
  score: number;
  text: string;
  source_uri: string | null;
  title: string | null;
};

export type MemorySearchResponse = {
  hits: MemorySearchHit[];
};

export type MemoryNoteCreateRequest = {
  text: string;
  title?: string | null;
  sensitivity?: 'private' | 'shared' | 'restricted';
  retention_class?: 'keep' | 'ttl_30d' | 'ttl_90d' | 'legal_hold';
};

export type DocumentSensitivity = 'private' | 'shared' | 'restricted';
export type DocumentRetentionClass = 'keep' | 'ttl_30d' | 'ttl_90d' | 'legal_hold';

export type DocumentUploadRequest = {
  file: Blob;
  filename: string;
  declared_size: number;
  source_timestamp: string;
  title?: string | null;
  sensitivity: DocumentSensitivity;
  retention_class: DocumentRetentionClass;
};

export type DocumentUploadResponse = {
  id: string;
  kind: 'document.ingest';
  status: 'waiting_approval';
  risk_level: 'local_mutation';
  action_digest: string;
};

export type TaskResponse = {
  id: string;
  kind: string;
  risk_level: string;
  status: string;
  trace_id: string;
  priority: number;
  attempt_count: number;
  max_attempts: number;
  result: Record<string, JsonValue> | null;
  error_text: string | null;
  approval_expires_at: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskPayloadSummary = {
  format: 'sorted-pretty-json-v1';
  encoding: 'utf-8';
  byte_length: number;
  character_count: number;
  page_count: number;
  page_size_characters: number;
  top_level_field_count: number;
  preview_head: string;
  preview_tail: string;
  preview_omitted_characters: number;
};

export type TaskDetailResponse = TaskResponse & {
  payload_summary: TaskPayloadSummary;
  action_digest: string | null;
};

export type TaskPayloadPageResponse = {
  task_id: string;
  action_digest: string | null;
  format: 'sorted-pretty-json-v1';
  encoding: 'utf-8';
  page_index: number;
  page_count: number;
  page_size_characters: number;
  character_start: number;
  character_end: number;
  content: string;
};
