import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiConfigurationError, isAbortError, type MongarsClient } from '@/lib/api';
import { useMongars } from '@/providers/mongars-provider';
import type {
  ChatRequest,
  ChatResponse,
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

type QueryOptions = {
  auto?: boolean;
};

type TasksQueryOptions = QueryOptions & {
  limit?: number;
};

export type QueryResult<T> = {
  data: T | null;
  error: Error | null;
  isLoading: boolean;
  refresh: () => Promise<T>;
  cancel: () => void;
};

export type MutationResult<TInput, TData> = {
  data: TData | null;
  error: Error | null;
  isPending: boolean;
  mutate: (input: TInput) => Promise<TData>;
  cancel: () => void;
  reset: () => void;
};

export type StreamingChatResult = {
  data: ChatResponse | null;
  draftText: string;
  error: Error | null;
  isPending: boolean;
  sessionId: string | null;
  sources: ChatStreamSource[];
  traceId: string | null;
  mutate: (input: ChatRequest) => Promise<ChatResponse>;
  cancel: () => void;
  reset: () => void;
};

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error('The monGARS request failed.');
}

function requireClient(
  client: MongarsClient | null,
  configurationError: ApiConfigurationError | null,
): MongarsClient {
  if (!client) {
    throw configurationError ?? new ApiConfigurationError('The monGARS API is not configured.');
  }
  return client;
}

function useAbortableQuery<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  auto: boolean,
): QueryResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(auto);
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(true);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
  }, []);

  const refresh = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    const requestId = ++requestIdRef.current;
    controllerRef.current = controller;
    setIsLoading(true);
    setError(null);

    try {
      const result = await loader(controller.signal);
      if (mountedRef.current && requestId === requestIdRef.current) {
        setData(result);
      }
      return result;
    } catch (requestError) {
      if (
        mountedRef.current &&
        requestId === requestIdRef.current &&
        !isAbortError(requestError)
      ) {
        setError(toError(requestError));
      }
      throw requestError;
    } finally {
      if (mountedRef.current && requestId === requestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, [loader]);

  useEffect(() => {
    mountedRef.current = true;
    if (auto) {
      refresh().catch(() => undefined);
    } else {
      setIsLoading(false);
    }

    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, [auto, refresh]);

  return { data, error, isLoading, refresh, cancel };
}

function useAbortableMutation<TInput, TData>(
  executor: (input: TInput, signal: AbortSignal) => Promise<TData>,
): MutationResult<TInput, TData> {
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isPending, setIsPending] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    requestIdRef.current += 1;
    setData(null);
    setError(null);
    setIsPending(false);
  }, []);

  const mutate = useCallback(
    async (input: TInput) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      const requestId = ++requestIdRef.current;
      controllerRef.current = controller;
      setIsPending(true);
      setError(null);

      try {
        const result = await executor(input, controller.signal);
        if (mountedRef.current && requestId === requestIdRef.current) {
          setData(result);
        }
        return result;
      } catch (requestError) {
        if (
          mountedRef.current &&
          requestId === requestIdRef.current &&
          !isAbortError(requestError)
        ) {
          setError(toError(requestError));
        }
        throw requestError;
      } finally {
        if (mountedRef.current && requestId === requestIdRef.current) {
          setIsPending(false);
        }
      }
    },
    [executor],
  );

  return { data, error, isPending, mutate, cancel, reset };
}

export function useReadiness(options: QueryOptions = {}): QueryResult<ReadinessResponse> {
  const { client, configurationError } = useMongars();
  const loader = useCallback(
    (signal: AbortSignal) => requireClient(client, configurationError).readiness({ signal }),
    [client, configurationError],
  );
  return useAbortableQuery(loader, options.auto ?? true);
}

export function useTasks(options: TasksQueryOptions = {}): QueryResult<TaskResponse[]> {
  const { client, configurationError } = useMongars();
  const limit = options.limit ?? 50;
  const loader = useCallback(
    (signal: AbortSignal) =>
      requireClient(client, configurationError).listTasks(limit, { signal }),
    [client, configurationError, limit],
  );
  return useAbortableQuery(loader, options.auto ?? true);
}

export function useTaskDetail(
  taskId: string,
  options: QueryOptions = {},
): QueryResult<TaskDetailResponse> {
  const { client, configurationError } = useMongars();
  const loader = useCallback(
    (signal: AbortSignal) =>
      requireClient(client, configurationError).getTask(taskId, { signal }),
    [client, configurationError, taskId],
  );
  return useAbortableQuery(loader, options.auto ?? true);
}

export function useTaskPayloadPage(
  taskId: string,
  page: number,
  actionDigest: string | null,
  pageCount: number,
  pageSizeCharacters: number,
  options: QueryOptions = {},
): QueryResult<TaskPayloadPageResponse> {
  const { client, configurationError } = useMongars();
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!actionDigest) throw new Error('The protected review has no action digest.');
    const payloadPage = await requireClient(client, configurationError).getTaskPayloadPage(
      taskId,
      page,
      { signal },
    );
    if (
      payloadPage.task_id !== taskId ||
      payloadPage.action_digest !== actionDigest ||
      payloadPage.format !== 'sorted-pretty-json-v1' ||
      payloadPage.encoding !== 'utf-8' ||
      payloadPage.page_index !== page ||
      payloadPage.page_count !== pageCount ||
      payloadPage.page_size_characters !== pageSizeCharacters ||
      payloadPage.character_start !== page * pageSizeCharacters ||
      payloadPage.character_end < payloadPage.character_start ||
      payloadPage.character_end - payloadPage.character_start > pageSizeCharacters ||
      payloadPage.content.length > pageSizeCharacters * 2
    ) {
      throw new Error('The payload page did not match the protected review digest.');
    }
    return payloadPage;
  }, [
    actionDigest,
    client,
    configurationError,
    page,
    pageCount,
    pageSizeCharacters,
    taskId,
  ]);
  return useAbortableQuery(loader, options.auto ?? true);
}

export function useChat(): MutationResult<ChatRequest, ChatResponse> {
  const { client, configurationError } = useMongars();
  const executor = useCallback(
    (request: ChatRequest, signal: AbortSignal) =>
      requireClient(client, configurationError).chat(request, { signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function useStreamingChat(): StreamingChatResult {
  const { client, configurationError } = useMongars();
  const [data, setData] = useState<ChatResponse | null>(null);
  const [draftText, setDraftText] = useState('');
  const [error, setError] = useState<Error | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sources, setSources] = useState<ChatStreamSource[]>([]);
  const [traceId, setTraceId] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    requestIdRef.current += 1;
    setData(null);
    setDraftText('');
    setSources([]);
    setSessionId(null);
    setTraceId(null);
    setError(null);
    setIsPending(false);
  }, []);

  const mutate = useCallback(
    async (input: ChatRequest) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      const requestId = ++requestIdRef.current;
      controllerRef.current = controller;
      setData(null);
      setDraftText('');
      setSources([]);
      setTraceId(null);
      setError(null);
      setIsPending(true);
      const effectiveInput =
        input.session_id == null && sessionId
          ? { ...input, session_id: sessionId }
          : input;

      try {
        const response = await requireClient(client, configurationError).streamChat(effectiveInput, {
          signal: controller.signal,
          onStart: (frame) => {
            if (mountedRef.current && requestId === requestIdRef.current) {
              setDraftText('');
              setSources([]);
              setSessionId(frame.session_id);
              setTraceId(frame.trace_id);
            }
          },
          onSources: (nextSources) => {
            if (mountedRef.current && requestId === requestIdRef.current) {
              setSources(nextSources);
            }
          },
          onDelta: (text) => {
            if (mountedRef.current && requestId === requestIdRef.current) {
              setDraftText((current) => current + text);
            }
          },
        });
        if (mountedRef.current && requestId === requestIdRef.current) {
          setData(response);
          setDraftText(response.answer);
          setSessionId(response.session_id);
          setTraceId(response.trace_id);
        }
        return response;
      } catch (requestError) {
        if (mountedRef.current && requestId === requestIdRef.current) {
          // A partial draft is not a committed assistant response. Discard it on every
          // protocol, inference, or cancellation failure. Keep start-frame identity so the
          // next turn remains in the same server-side autobiographical session.
          setDraftText('');
          setSources([]);
          if (!isAbortError(requestError)) setError(toError(requestError));
        }
        throw requestError;
      } finally {
        if (mountedRef.current && requestId === requestIdRef.current) {
          setIsPending(false);
        }
      }
    },
    [client, configurationError, sessionId],
  );

  return {
    data,
    draftText,
    error,
    isPending,
    sessionId,
    sources,
    traceId,
    mutate,
    cancel,
    reset,
  };
}

export function useMemorySearch(): MutationResult<
  MemorySearchRequest,
  MemorySearchResponse
> {
  const { client, configurationError } = useMongars();
  const executor = useCallback(
    (request: MemorySearchRequest, signal: AbortSignal) =>
      requireClient(client, configurationError).searchMemory(request, { signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function useCreateMemoryNote(): MutationResult<MemoryNoteCreateRequest, TaskResponse> {
  const { client, configurationError } = useMongars();
  const executor = useCallback(
    (request: MemoryNoteCreateRequest, signal: AbortSignal) =>
      requireClient(client, configurationError).createMemoryNote(request, { signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function useDocumentUpload(): MutationResult<
  DocumentUploadRequest,
  DocumentUploadResponse
> {
  const { client, configurationError } = useMongars();
  const executor = useCallback(
    (request: DocumentUploadRequest, signal: AbortSignal) =>
      requireClient(client, configurationError).uploadDocument(request, { signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function useApproveTask(): MutationResult<
  { taskId: string; actionDigest: string },
  TaskResponse
> {
  const { client, configurationError } = useMongars();
  const executor = useCallback(
    (input: { taskId: string; actionDigest: string }, signal: AbortSignal) =>
      requireClient(client, configurationError).approveTask(
        input.taskId,
        input.actionDigest,
        { signal },
      ),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function useCancelTask(): MutationResult<string, void> {
  const { client, configurationError } = useMongars();
  const executor = useCallback(
    (taskId: string, signal: AbortSignal) =>
      requireClient(client, configurationError).cancelTask(taskId, { signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}
