import { fetch as expoFetch, type FetchRequestInit } from 'expo/fetch';

import { apiTokenStore, type ApiTokenStore } from '@/lib/api-token';
import { NdjsonChatDecoder } from '@/lib/api/chat-stream';
import {
  ApiConfigurationError,
  type ApiTransportSecurity,
  assertSecureCredentialTransport,
  getApiTransportSecurity,
  getMongarsApiBaseUrl,
  getMongarsApiOrigin,
  normalizeMongarsApiBaseUrl,
} from '@/lib/api-origin';
import type {
  ChatRequest,
  ChatResponse,
  ChatStreamFrame,
  DocumentUploadRequest,
  DocumentUploadResponse,
  MemoryNoteCreateRequest,
  MemorySearchRequest,
  MemorySearchResponse,
  ReadinessResponse,
  TaskDetailResponse,
  TaskPayloadPageResponse,
  TaskResponse,
} from '@/types/mongars-api';

export type ApiCallOptions = {
  signal?: AbortSignal;
};

export type FetchImplementation = (
  input: string,
  init?: FetchRequestInit,
) => Promise<Response>;

export type MongarsClientOptions = {
  baseUrl?: string;
  fetcher?: FetchImplementation;
  tokenStore?: ApiTokenStore;
};

export type ChatStreamCallbacks = {
  onStart?: (streamId: string) => void;
  onAttempt?: (attempt: number) => void;
  onReset?: (attempt: number) => void;
  onDelta?: (text: string, attempt: number) => void;
};

type RequestOptions = ApiCallOptions & {
  method?: 'GET' | 'POST';
  body?: unknown;
  multipartBody?: FormData;
  authenticated?: boolean;
  acceptedStatuses?: readonly number[];
};

type ChatStreamState = {
  activeAttempt: number;
  provisionalText: string;
  finalResponse: ChatResponse | null;
  terminal: boolean;
};

export {
  ApiConfigurationError,
  type ApiTransportSecurity,
  assertSecureCredentialTransport,
  getApiTransportSecurity,
  getMongarsApiBaseUrl,
  getMongarsApiOrigin,
  normalizeMongarsApiBaseUrl,
} from '@/lib/api-origin';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(
    message: string,
    options: { status: number; code: string; detail?: unknown; cause?: unknown },
  ) {
    super(message, { cause: options.cause });
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.detail = options.detail;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function readResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorCode(body: unknown, status: number): string {
  if (isRecord(body)) {
    if (typeof body.code === 'string') {
      return body.code;
    }
    if (isRecord(body.detail) && typeof body.detail.code === 'string') {
      return body.detail.code;
    }
  }

  return status === 401 ? 'UNAUTHORIZED' : `HTTP_${status}`;
}

function errorMessage(body: unknown, status: number): string {
  if (isRecord(body)) {
    if (typeof body.detail === 'string') {
      return body.detail;
    }
    if (isRecord(body.detail) && typeof body.detail.message === 'string') {
      return body.detail.message;
    }
    if (typeof body.message === 'string') {
      return body.message;
    }
    if (Array.isArray(body.detail)) {
      return 'The server rejected the request data.';
    }
  }
  if (typeof body === 'string' && body.trim()) {
    return body;
  }

  return `monGARS request failed with HTTP ${status}.`;
}

export class MongarsClient {
  readonly baseUrl: string;
  private readonly fetcher: FetchImplementation;
  private readonly tokenStore: ApiTokenStore;

  constructor(options: MongarsClientOptions = {}) {
    this.baseUrl = getMongarsApiBaseUrl(options.baseUrl);
    this.fetcher = options.fetcher ?? expoFetch;
    this.tokenStore = options.tokenStore ?? apiTokenStore;
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    if (options.body !== undefined && options.multipartBody) {
      throw new TypeError('A request cannot contain JSON and multipart bodies together.');
    }
    const authenticated = options.authenticated ?? true;
    const headers = new Headers({ Accept: 'application/json' });

    if (options.body !== undefined) {
      headers.set('Content-Type', 'application/json');
    }

    if (authenticated) {
      // Never read a Keychain credential until the destination is known to protect it. This makes
      // the policy hold even when a caller bypasses the provider/UI and calls the client directly.
      assertSecureCredentialTransport(this.baseUrl);
      const token = await this.tokenStore.read(getMongarsApiOrigin(this.baseUrl));
      if (!token) {
        throw new ApiError('Enter the monGARS API token to continue.', {
          status: 401,
          code: 'AUTH_REQUIRED',
        });
      }
      headers.set('Authorization', `Bearer ${token}`);
    }

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, {
        method: options.method ?? 'GET',
        headers,
        body:
          options.multipartBody ??
          (options.body === undefined ? undefined : JSON.stringify(options.body)),
        signal: options.signal,
      });
    } catch (error) {
      if (isAbortError(error)) {
        throw error;
      }
      throw new ApiError('Unable to reach the monGARS server.', {
        status: 0,
        code: 'NETWORK_ERROR',
        cause: error,
      });
    }

    const body = await readResponseBody(response);
    const accepted = response.ok || options.acceptedStatuses?.includes(response.status);
    if (!accepted) {
      if (response.status === 401 && authenticated) {
        // A rejected bearer credential must not be retried indefinitely. The token store publishes
        // the change so mounted provider/UI state is invalidated at the same time.
        await this.tokenStore.clear().catch(() => undefined);
      }
      throw new ApiError(errorMessage(body, response.status), {
        status: response.status,
        code: errorCode(body, response.status),
        detail: body,
      });
    }

    return body as T;
  }

  health(options: ApiCallOptions = {}): Promise<{ status: 'ok' }> {
    return this.request('/v1/healthz', { ...options, authenticated: false });
  }

  readiness(options: ApiCallOptions = {}): Promise<ReadinessResponse> {
    return this.request('/v1/readyz', {
      ...options,
      acceptedStatuses: [503],
    });
  }

  chat(request: ChatRequest, options: ApiCallOptions = {}): Promise<ChatResponse> {
    return this.request('/v1/chat', { ...options, method: 'POST', body: request });
  }

  async chatStream(
    request: ChatRequest,
    callbacks: ChatStreamCallbacks = {},
    options: ApiCallOptions = {},
  ): Promise<ChatResponse> {
    assertSecureCredentialTransport(this.baseUrl);
    const token = await this.tokenStore.read(getMongarsApiOrigin(this.baseUrl));
    if (!token) {
      throw new ApiError('Enter the monGARS API token to continue.', {
        status: 401,
        code: 'AUTH_REQUIRED',
      });
    }
    const headers = new Headers({
      Accept: 'application/x-ndjson',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    });

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}/v1/chat/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify(request),
        signal: options.signal,
      });
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError('Unable to reach the monGARS server.', {
        status: 0,
        code: 'NETWORK_ERROR',
        cause: error,
      });
    }

    if (!response.ok) {
      const body = await readResponseBody(response);
      if (response.status === 401) {
        await this.tokenStore.clear().catch(() => undefined);
      }
      throw new ApiError(errorMessage(body, response.status), {
        status: response.status,
        code: errorCode(body, response.status),
        detail: body,
      });
    }
    if (!response.body) {
      throw new ApiError('The monGARS server returned no chat stream.', {
        status: response.status,
        code: 'EMPTY_STREAM',
      });
    }

    const state: ChatStreamState = {
      activeAttempt: 0,
      provisionalText: '',
      finalResponse: null,
      terminal: false,
    };
    const reader = response.body.getReader();
    const textDecoder = new TextDecoder();
    const ndjson = new NdjsonChatDecoder();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const frames = ndjson.push(textDecoder.decode(value, { stream: true }));
        for (const frame of frames) applyStreamFrame(state, frame, callbacks);
      }
      for (const frame of ndjson.push(textDecoder.decode())) {
        applyStreamFrame(state, frame, callbacks);
      }
      for (const frame of ndjson.finish()) {
        applyStreamFrame(state, frame, callbacks);
      }
    } catch (error) {
      await reader.cancel().catch(() => undefined);
      if (isAbortError(error) || error instanceof ApiError) throw error;
      throw new ApiError('The monGARS chat stream was interrupted.', {
        status: 0,
        code: 'STREAM_ERROR',
        cause: error,
      });
    } finally {
      reader.releaseLock();
    }

    const finalResponse = state.finalResponse;
    if (!finalResponse) {
      throw new ApiError('The monGARS stream ended without a final response.', {
        status: 0,
        code: 'INCOMPLETE_STREAM',
      });
    }
    if (state.provisionalText && state.provisionalText !== finalResponse.answer) {
      throw new ApiError('The streamed text did not match the validated answer.', {
        status: 0,
        code: 'STREAM_MISMATCH',
      });
    }
    return finalResponse;
  }

  searchMemory(
    request: MemorySearchRequest,
    options: ApiCallOptions = {},
  ): Promise<MemorySearchResponse> {
    return this.request('/v1/memory/search', {
      ...options,
      method: 'POST',
      body: request,
    });
  }

  createMemoryNote(
    request: MemoryNoteCreateRequest,
    options: ApiCallOptions = {},
  ): Promise<TaskResponse> {
    return this.request('/v1/memory/documents', {
      ...options,
      method: 'POST',
      body: request,
    });
  }

  uploadDocument(
    request: DocumentUploadRequest,
    options: ApiCallOptions = {},
  ): Promise<DocumentUploadResponse> {
    if (
      !Number.isSafeInteger(request.declared_size) ||
      request.declared_size <= 0 ||
      request.file.size !== request.declared_size
    ) {
      throw new ApiError('The selected document size could not be verified.', {
        status: 0,
        code: 'INVALID_DOCUMENT_SIZE',
      });
    }

    const sourceTimestamp = new Date(request.source_timestamp);
    if (!Number.isFinite(sourceTimestamp.getTime())) {
      throw new ApiError('The selected document timestamp is invalid.', {
        status: 0,
        code: 'INVALID_DOCUMENT_TIMESTAMP',
      });
    }

    const multipartBody = new FormData();
    multipartBody.append('file', request.file, request.filename);
    multipartBody.append('declared_size', String(request.declared_size));
    multipartBody.append('source_timestamp', sourceTimestamp.toISOString());
    multipartBody.append('sensitivity', request.sensitivity);
    multipartBody.append('retention_class', request.retention_class);
    if (request.title?.trim()) {
      multipartBody.append('title', request.title.trim());
    }

    // Do not set Content-Type here: expo/fetch supplies the multipart boundary for this FormData.
    return this.request('/v1/documents', {
      ...options,
      method: 'POST',
      multipartBody,
    });
  }

  listTasks(limit = 50, options: ApiCallOptions = {}): Promise<TaskResponse[]> {
    const safeLimit = Math.max(1, Math.min(100, Math.trunc(limit)));
    return this.request(`/v1/tasks?limit=${safeLimit}`, options);
  }

  getTask(taskId: string, options: ApiCallOptions = {}): Promise<TaskDetailResponse> {
    return this.request(`/v1/tasks/${encodeURIComponent(taskId)}`, options);
  }

  getTaskPayloadPage(
    taskId: string,
    page: number,
    options: ApiCallOptions = {},
  ): Promise<TaskPayloadPageResponse> {
    const safePage = Math.max(0, Math.min(100_000, Math.trunc(page)));
    return this.request(
      `/v1/tasks/${encodeURIComponent(taskId)}/payload?page=${safePage}`,
      options,
    );
  }

  approveTask(
    taskId: string,
    actionDigest: string,
    options: ApiCallOptions = {},
  ): Promise<TaskResponse> {
    return this.request(`/v1/tasks/${encodeURIComponent(taskId)}/approve`, {
      ...options,
      method: 'POST',
      body: { action_digest: actionDigest },
    });
  }

  async cancelTask(taskId: string, options: ApiCallOptions = {}): Promise<void> {
    await this.request<null>(`/v1/tasks/${encodeURIComponent(taskId)}/cancel`, {
      ...options,
      method: 'POST',
    });
  }
}

function applyStreamFrame(
  state: ChatStreamState,
  frame: ChatStreamFrame,
  callbacks: ChatStreamCallbacks,
): void {
  if (state.terminal) {
    throw new ApiError('The monGARS stream continued after a terminal frame.', {
      status: 0,
      code: 'STREAM_PROTOCOL_ERROR',
    });
  }
  switch (frame.type) {
    case 'start':
      callbacks.onStart?.(frame.stream_id);
      return;
    case 'attempt':
      state.activeAttempt = frame.attempt;
      callbacks.onAttempt?.(frame.attempt);
      return;
    case 'reset':
      state.provisionalText = '';
      callbacks.onReset?.(frame.attempt);
      return;
    case 'delta':
      if (frame.attempt !== state.activeAttempt) {
        throw new ApiError('The monGARS stream delta belongs to another attempt.', {
          status: 0,
          code: 'STREAM_PROTOCOL_ERROR',
        });
      }
      state.provisionalText += frame.text;
      callbacks.onDelta?.(frame.text, frame.attempt);
      return;
    case 'final':
      state.finalResponse = frame.response;
      state.terminal = true;
      return;
    case 'error':
      state.provisionalText = '';
      state.terminal = true;
      throw new ApiError('The streamed monGARS response failed.', {
        status: 200,
        code: frame.code,
        detail: frame,
      });
  }
}

let defaultClient: MongarsClient | null = null;

export function getMongarsClient(): MongarsClient {
  defaultClient ??= new MongarsClient();
  return defaultClient;
}
