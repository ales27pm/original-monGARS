import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiConfigurationError, isAbortError } from '@/lib/api';
import { AdaptationClient } from '@/lib/api/adaptation';
import { useMongars } from '@/providers/mongars-provider';
import type {
  ExplicitFeedbackRequest,
  ExplicitFeedbackResponse,
  PersonalityExportResponse,
  PersonalityRevision,
  PersonalitySnapshot,
} from '@/types/adaptation';

type QueryOptions = {
  auto?: boolean;
};

type RevisionsQueryOptions = QueryOptions & {
  limit?: number;
};

export type AdaptationQueryResult<T> = {
  data: T | null;
  error: Error | null;
  isLoading: boolean;
  refresh: () => Promise<T>;
  cancel: () => void;
};

export type AdaptationMutationResult<TInput, TData> = {
  data: TData | null;
  error: Error | null;
  isPending: boolean;
  mutate: (input: TInput) => Promise<TData>;
  cancel: () => void;
  reset: () => void;
};

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error('The monGARS adaptation request failed.');
}

function useAdaptationClient(): {
  client: AdaptationClient | null;
  configurationError: ApiConfigurationError | null;
} {
  const { client, configurationError } = useMongars();
  const adaptationClient = useMemo(
    () => (client ? new AdaptationClient({ baseUrl: client.baseUrl }) : null),
    [client],
  );
  return { client: adaptationClient, configurationError };
}

function requireClient(
  client: AdaptationClient | null,
  configurationError: ApiConfigurationError | null,
): AdaptationClient {
  if (!client) {
    throw configurationError ?? new ApiConfigurationError('The monGARS API is not configured.');
  }
  return client;
}

function useAbortableQuery<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  auto: boolean,
): AdaptationQueryResult<T> {
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
    if (auto) refresh().catch(() => undefined);
    else setIsLoading(false);

    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, [auto, refresh]);

  return { data, error, isLoading, refresh, cancel };
}

function useAbortableMutation<TInput, TData>(
  executor: (input: TInput, signal: AbortSignal) => Promise<TData>,
): AdaptationMutationResult<TInput, TData> {
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

export function useSubmitFeedback(): AdaptationMutationResult<
  ExplicitFeedbackRequest,
  ExplicitFeedbackResponse
> {
  const { client, configurationError } = useAdaptationClient();
  const executor = useCallback(
    (request: ExplicitFeedbackRequest, signal: AbortSignal) =>
      requireClient(client, configurationError).submitFeedback(request, { signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function usePersonalityProfile(
  options: QueryOptions = {},
): AdaptationQueryResult<PersonalitySnapshot> {
  const { client, configurationError } = useAdaptationClient();
  const loader = useCallback(
    (signal: AbortSignal) =>
      requireClient(client, configurationError).getProfile({ signal }),
    [client, configurationError],
  );
  return useAbortableQuery(loader, options.auto ?? true);
}

export function usePersonalityRevisions(
  options: RevisionsQueryOptions = {},
): AdaptationQueryResult<PersonalityRevision[]> {
  const { client, configurationError } = useAdaptationClient();
  const limit = options.limit ?? 50;
  const loader = useCallback(
    (signal: AbortSignal) =>
      requireClient(client, configurationError).getRevisions(limit, { signal }),
    [client, configurationError, limit],
  );
  return useAbortableQuery(loader, options.auto ?? true);
}

export function usePersonalityExport(
  options: QueryOptions = {},
): AdaptationQueryResult<PersonalityExportResponse> {
  const { client, configurationError } = useAdaptationClient();
  const loader = useCallback(
    (signal: AbortSignal) =>
      requireClient(client, configurationError).exportProfile({ signal }),
    [client, configurationError],
  );
  return useAbortableQuery(loader, options.auto ?? false);
}

export function useResetPersonality(): AdaptationMutationResult<void, PersonalitySnapshot> {
  const { client, configurationError } = useAdaptationClient();
  const executor = useCallback(
    (_input: void, signal: AbortSignal) =>
      requireClient(client, configurationError).resetProfile({ signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}

export function useDeletePersonality(): AdaptationMutationResult<void, void> {
  const { client, configurationError } = useAdaptationClient();
  const executor = useCallback(
    (_input: void, signal: AbortSignal) =>
      requireClient(client, configurationError).deleteProfile({ signal }),
    [client, configurationError],
  );
  return useAbortableMutation(executor);
}
