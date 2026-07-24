import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiConfigurationError, isAbortError } from '@/lib/api';
import { useMongars } from '@/providers/mongars-provider';
import type { ChatRequest, ChatResponse } from '@/types/mongars-api';

export type StreamingChatResult = {
  data: ChatResponse | null;
  error: Error | null;
  isPending: boolean;
  partialText: string;
  attempt: number;
  mutate: (request: ChatRequest) => Promise<ChatResponse>;
  cancel: () => void;
  reset: () => void;
};

export function useStreamingChat(): StreamingChatResult {
  const { client, configurationError } = useMongars();
  const [data, setData] = useState<ChatResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [partialText, setPartialText] = useState('');
  const [attempt, setAttempt] = useState(0);
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
    setPartialText('');
    setAttempt(0);
  }, []);

  const mutate = useCallback(
    async (request: ChatRequest) => {
      if (!client) {
        throw (
          configurationError ?? new ApiConfigurationError('The monGARS API is not configured.')
        );
      }
      controllerRef.current?.abort();
      const controller = new AbortController();
      const requestId = ++requestIdRef.current;
      controllerRef.current = controller;
      setData(null);
      setError(null);
      setIsPending(true);
      setPartialText('');
      setAttempt(0);

      try {
        const response = await client.chatStream(
          request,
          {
            onAttempt: (nextAttempt) => {
              if (mountedRef.current && requestId === requestIdRef.current) {
                setAttempt(nextAttempt);
              }
            },
            onReset: (nextAttempt) => {
              if (mountedRef.current && requestId === requestIdRef.current) {
                setAttempt(nextAttempt);
                setPartialText('');
              }
            },
            onDelta: (text) => {
              if (mountedRef.current && requestId === requestIdRef.current) {
                setPartialText((current) => current + text);
              }
            },
          },
          { signal: controller.signal },
        );
        if (mountedRef.current && requestId === requestIdRef.current) {
          setData(response);
          setPartialText(response.answer);
        }
        return response;
      } catch (requestError) {
        if (
          mountedRef.current &&
          requestId === requestIdRef.current &&
          !isAbortError(requestError)
        ) {
          setPartialText('');
          setError(
            requestError instanceof Error
              ? requestError
              : new Error('The monGARS stream failed.'),
          );
        }
        throw requestError;
      } finally {
        if (mountedRef.current && requestId === requestIdRef.current) {
          setIsPending(false);
        }
      }
    },
    [client, configurationError],
  );

  return { data, error, isPending, partialText, attempt, mutate, cancel, reset };
}
