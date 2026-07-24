import { fetch as expoFetch, type FetchRequestInit } from 'expo/fetch';

import { apiTokenStore, type ApiTokenStore } from '@/lib/api-token';
import {
  assertSecureCredentialTransport,
  getMongarsApiOrigin,
  normalizeMongarsApiBaseUrl,
} from '@/lib/api-origin';
import { ApiError, isAbortError, type FetchImplementation } from '@/lib/api/client';
import type {
  ExplicitFeedbackRequest,
  ExplicitFeedbackResponse,
  PersonalityExportResponse,
  PersonalityRevision,
  PersonalitySnapshot,
} from '@/types/adaptation';

export type AdaptationCallOptions = {
  signal?: AbortSignal;
};

export type AdaptationClientOptions = {
  baseUrl: string;
  fetcher?: FetchImplementation;
  tokenStore?: ApiTokenStore;
};

type RequestOptions = AdaptationCallOptions & {
  method?: 'DELETE' | 'GET' | 'POST';
  body?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function responseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function responseErrorCode(body: unknown, status: number): string {
  if (isRecord(body)) {
    if (typeof body.code === 'string') return body.code;
    if (isRecord(body.detail) && typeof body.detail.code === 'string') {
      return body.detail.code;
    }
  }
  return status === 401 ? 'UNAUTHORIZED' : `HTTP_${status}`;
}

function responseErrorMessage(body: unknown, status: number): string {
  if (isRecord(body)) {
    if (typeof body.detail === 'string') return body.detail;
    if (isRecord(body.detail) && typeof body.detail.message === 'string') {
      return body.detail.message;
    }
    if (typeof body.message === 'string') return body.message;
    if (Array.isArray(body.detail)) return 'The server rejected the adaptation request data.';
  }
  if (typeof body === 'string' && body.trim()) return body;
  return `monGARS adaptation request failed with HTTP ${status}.`;
}

export class AdaptationClient {
  readonly baseUrl: string;
  private readonly fetcher: FetchImplementation;
  private readonly tokenStore: ApiTokenStore;

  constructor(options: AdaptationClientOptions) {
    this.baseUrl = normalizeMongarsApiBaseUrl(options.baseUrl);
    this.fetcher = options.fetcher ?? expoFetch;
    this.tokenStore = options.tokenStore ?? apiTokenStore;
  }

  private async authorize(headers: Headers): Promise<void> {
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

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers({ Accept: 'application/json' });
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    await this.authorize(headers);

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: options.signal,
      } satisfies FetchRequestInit);
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError('Unable to reach the monGARS server.', {
        status: 0,
        code: 'NETWORK_ERROR',
        cause: error,
      });
    }

    const body = await responseBody(response);
    if (!response.ok) {
      if (response.status === 401) {
        await this.tokenStore.clear().catch(() => undefined);
      }
      throw new ApiError(responseErrorMessage(body, response.status), {
        status: response.status,
        code: responseErrorCode(body, response.status),
        detail: body,
      });
    }
    return body as T;
  }

  submitFeedback(
    request: ExplicitFeedbackRequest,
    options: AdaptationCallOptions = {},
  ): Promise<ExplicitFeedbackResponse> {
    return this.request('/v1/adaptation/feedback', {
      ...options,
      method: 'POST',
      body: request,
    });
  }

  getProfile(options: AdaptationCallOptions = {}): Promise<PersonalitySnapshot> {
    return this.request('/v1/adaptation/profile', options);
  }

  getRevisions(
    limit = 50,
    options: AdaptationCallOptions = {},
  ): Promise<PersonalityRevision[]> {
    const safeLimit = Math.max(1, Math.min(100, Math.trunc(limit)));
    return this.request(`/v1/adaptation/profile/revisions?limit=${safeLimit}`, options);
  }

  exportProfile(options: AdaptationCallOptions = {}): Promise<PersonalityExportResponse> {
    return this.request('/v1/adaptation/personality/export', options);
  }

  resetProfile(options: AdaptationCallOptions = {}): Promise<PersonalitySnapshot> {
    return this.request('/v1/adaptation/personality/reset', {
      ...options,
      method: 'POST',
    });
  }

  async deleteProfile(options: AdaptationCallOptions = {}): Promise<void> {
    await this.request<null>('/v1/adaptation/personality', {
      ...options,
      method: 'DELETE',
    });
  }
}
