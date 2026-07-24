import { fetch as expoFetch, type FetchRequestInit } from 'expo/fetch';

import { apiTokenStore, type ApiTokenStore } from '@/lib/api-token';
import {
  ApiConfigurationError,
  type ApiTransportSecurity,
  assertSecureCredentialTransport,
  getApiTransportSecurity,
  getMongarsApiBaseUrl,
  getMongarsApiOrigin,
  normalizeMongarsApiBaseUrl,
} from '@/lib/api-origin';
import { ChatNdjsonDecoder, ChatStreamProtocolError } from '@/lib/api/ndjson';
import type {
  ChatRequest,
  ChatResponse,
  ChatStreamFrame,
  ChatStreamSource,
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

export type ChatStreamHandlers = {
  onStart?: (frame: Extract<ChatStreamFrame, { type: 'start' }>) => void | Promise<void>;
  onSources?: (sources: ChatStreamSource[]) => void | Promise<void>;
  onDelta?: (text: string) => void | Promise<void>;
};

export type ChatStreamOptions = ApiCallOptions & ChatStreamHandlers;

export type FetchImplementation = (
  input: string,
  init?: FetchRequestInit,
) => Promise<Response>;

export type MongarsClientOptions = {
  baseUrl?: string;
  fetcher?: FetchImplementation;
  tokenStore?: ApiTokenStore;
};

type RequestOptions = ApiCallOptions & {
  method?: 'GET' | 'POST';
  body?: unknown;
  multipartBody?: FormData;
  authenticated?: boolean;
  acceptedStatuses?: readonly number[];
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
      await this.authorize(headers);
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
      await this.rejectResponse(response.status, authenticated);
      throw new ApiError(errorMessage(body, response.status), {
        status: response.status,
        code: errorCode(body, response.status),
        detail: body,
      });
    }

    return body as T;
  }

  private async authorize(headers: Headers): Promise<void> {
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

  private async rejectResponse(status: number, authenticated: boolean): Promise<void> {
    if (status === 401 && authenticated) {
      // A rejected bearer credential must not be retried indefinitely. The token store publishes
      // the change so mounted provider/UI state is invalidated at the same time.
      await this.tokenStore.clear().catch(() => undefined);
    }
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

  async streamChat(
    request: ChatRequest,
    options: ChatStreamOptions = {},
  ): Promise<ChatResponse> {
    const headers = new Headers({
      Accept: 'application/x-ndjson',
      'Content-Type': 'application/json',
    });
    await this.authorize(headers);

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
      await this.rejectResponse(response.status, true);
      throw new ApiError(errorMessage(body, response.status), {
        status: response.status,
        code: errorCode(body, response.status),
        detail: body,
      });
    }

    const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase();
    if (mediaType !== 'application/x-ndjson') {
      await response.body?.cancel().catch(() => undefined);
      throw new ApiError('The monGARS server returned an unexpected chat stream format.', {
        status: response.status,
        code: 'STREAM_CONTENT_TYPE_ERROR',
      });
    }
    const reader = response.body?.getReader();
    if (!reader) {
      throw new ApiError('The monGARS server did not provide a readable chat stream.', {
        status: response.status,
        code: 'STREAM_BODY_MISSING',
      });
    }

    const decoder = new ChatNdjsonDecoder();
    let started: Extract<ChatStreamFrame, { type: 'start' }> | null = null;
    let sourcesSeen = false;
    let final: ChatResponse | null = null;

    const accept = async (frame: ChatStreamFrame): Promise<void> => {
      if (final) {
        throw new ChatStreamProtocolError('The chat stream emitted a frame after completion.');
      }
      if (frame.type === 'error') {
        throw new ApiError(`The monGARS chat stream failed (${frame.code}).`, {
          status: frame.retryable ? 503 : 422,
          code: frame.code,
          detail: frame,
        });
      }
      if (frame.type === 'start') {
        if (started) {
          throw new ChatStreamProtocolError('The chat stream emitted multiple start frames.');
        }
        started = frame;
        await options.onStart?.(frame);
        return;
      }
      if (!started) {
        throw new ChatStreamProtocolError('The chat stream emitted data before its start frame.');
      }
      if (frame.type === 'sources') {
        if (sourcesSeen) {
          throw new ChatStreamProtocolError('The chat stream emitted multiple source catalogs.');
        }
        sourcesSeen = true;
        await options.onSources?.(frame.sources);
        return;
      }
      if (!sourcesSeen) {
        throw new ChatStreamProtocolError('The chat stream emitted content before its sources.');
      }
      if (frame.type === 'delta') {
        await options.onDelta?.(frame.text);
        return;
      }
      if (frame.trace_id !== started.trace_id || frame.session_id !== started.session_id) {
        throw new ChatStreamProtocolError('The final chat frame changed the stream identity.');
      }
      const { type: _type, ...responseBody } = frame;
      final = responseBody;
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value) {
          for (const frame of decoder.push(value)) await accept(frame);
        }
      }
      for (const frame of decoder.finish()) await accept(frame);
    } catch (error) {
      await reader.cancel().catch(() => undefined);
      if (isAbortError(error) || error instanceof ApiError) throw error;
      throw new ApiError('The monGARS chat stream violated its protocol.', {
        status: response.status,
        code: 'STREAM_PROTOCOL_ERROR',
        cause: error,
      });
    } finally {
      reader.releaseLock();
    }

    if (!final) {
      throw new ApiError('The monGARS chat stream ended before a final answer.', {
        status: response.status,
        code: 'STREAM_INCOMPLETE',
      });
    }
    return final;
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

let defaultClient: MongarsClient | null = null;

export function getMongarsClient(): MongarsClient {
  defaultClient ??= new MongarsClient();
  return defaultClient;
}
