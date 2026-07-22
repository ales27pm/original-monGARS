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

export type TaskDetailResponse = TaskResponse & {
  payload: Record<string, JsonValue>;
  action_digest: string | null;
};
