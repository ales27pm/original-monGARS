import {
  createContext,
  type PropsWithChildren,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';

import {
  clearApiBaseUrl,
  readApiBaseUrl,
  resolveConfiguredApiBaseUrl,
  saveApiBaseUrl,
  subscribeApiBaseUrl,
} from '@/lib/api-base-url';
import {
  clearApiToken,
  type StoredCredential,
  readApiCredential,
  saveApiToken,
  subscribeApiCredential,
} from '@/lib/api-token';
import {
  ApiConfigurationError,
  type ApiTransportSecurity,
  getApiTransportSecurity,
  getMongarsApiOrigin,
  MongarsClient,
  normalizeMongarsApiBaseUrl,
} from '@/lib/api';

export type ApiTokenStatus = 'loading' | 'missing' | 'ready' | 'error';
export type ApiBaseUrlStatus = 'loading' | 'missing' | 'ready' | 'error';

export type MongarsContextValue = {
  client: MongarsClient | null;
  baseUrl: string | null;
  baseUrlStatus: ApiBaseUrlStatus;
  baseUrlStorageError: Error | null;
  configurationError: ApiConfigurationError | null;
  transportSecurity: ApiTransportSecurity | null;
  tokenStatus: ApiTokenStatus;
  hasToken: boolean;
  tokenStorageError: Error | null;
  saveBaseUrl: (baseUrl: string) => Promise<string>;
  clearBaseUrl: () => Promise<void>;
  saveToken: (token: string) => Promise<void>;
  clearToken: () => Promise<void>;
};

const MongarsContext = createContext<MongarsContextValue | null>(null);

type MongarsProviderProps = PropsWithChildren<{
  /** Primarily for previews and tests. Native users select their server in Settings. */
  baseUrl?: string;
}>;

export function MongarsProvider({ children, baseUrl: baseUrlOverride }: MongarsProviderProps) {
  const [savedBaseUrl, setSavedBaseUrl] = useState<string | null>(null);
  const [baseUrlStatus, setBaseUrlStatus] = useState<ApiBaseUrlStatus>('loading');
  const [baseUrlStorageError, setBaseUrlStorageError] = useState<Error | null>(null);
  const configuration = useMemo(() => {
    // Do not fall back to a build-time development URL until native storage has been checked. This
    // prevents a saved bearer token from racing onto a different origin during app startup.
    if (!baseUrlOverride && (baseUrlStatus === 'loading' || baseUrlStatus === 'error')) {
      return {
        baseUrl: null,
        client: null,
        transportSecurity: null,
        error:
          baseUrlStatus === 'error'
            ? new ApiConfigurationError(
                'The saved server URL could not be verified. Open Settings and save it again.',
              )
            : null,
      };
    }

    const configuredBaseUrl = resolveConfiguredApiBaseUrl({
      override: baseUrlOverride,
      persisted: savedBaseUrl,
      storageStatus: baseUrlStatus,
      buildTime: process.env.EXPO_PUBLIC_MONGARS_API_URL,
    });
    if (!configuredBaseUrl?.trim()) {
      return {
        baseUrl: null,
        client: null,
        transportSecurity: null,
        error: null,
      };
    }

    try {
      const baseUrl = normalizeMongarsApiBaseUrl(configuredBaseUrl);
      return {
        baseUrl,
        client: new MongarsClient({ baseUrl }),
        transportSecurity: getApiTransportSecurity(baseUrl),
        error: null,
      };
    } catch (error) {
      const configurationError =
        error instanceof ApiConfigurationError
          ? error
          : new ApiConfigurationError('Unable to configure the monGARS API client.');
      return {
        baseUrl: null,
        client: null,
        transportSecurity: null,
        error: configurationError,
      };
    }
  }, [baseUrlOverride, baseUrlStatus, savedBaseUrl]);
  const [credential, setCredential] = useState<StoredCredential | null>(null);
  const [credentialStatus, setCredentialStatus] = useState<ApiTokenStatus>('loading');
  const [credentialStorageError, setCredentialStorageError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    const unsubscribe = subscribeApiBaseUrl((baseUrl) => {
      if (active) {
        setSavedBaseUrl(baseUrl);
        setBaseUrlStatus(baseUrl ? 'ready' : 'missing');
        setBaseUrlStorageError(null);
      }
    });

    readApiBaseUrl().catch((error: unknown) => {
      if (active) {
        setBaseUrlStatus('error');
        setBaseUrlStorageError(
          error instanceof Error ? error : new Error('Unable to read the saved server URL.'),
        );
      }
    });

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    let active = true;
    const unsubscribe = subscribeApiCredential((nextCredential) => {
      if (active) {
        setCredential(nextCredential);
        setCredentialStatus(nextCredential ? 'ready' : 'missing');
        setCredentialStorageError(null);
      }
    });

    readApiCredential().catch((error: unknown) => {
      if (active) {
        setCredentialStatus('error');
        setCredentialStorageError(
          error instanceof Error ? error : new Error('Unable to read the API token.'),
        );
      }
    });

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const credentialOriginError = useMemo(() => {
    if (!credential || !configuration.baseUrl) return null;
    return credential.origin === getMongarsApiOrigin(configuration.baseUrl)
      ? null
      : new ApiConfigurationError(
          'The saved token belongs to another monGARS server. Authenticate again.',
        );
  }, [configuration.baseUrl, credential]);
  const tokenStatus: ApiTokenStatus = credentialOriginError ? 'error' : credentialStatus;
  const tokenStorageError = credentialOriginError ?? credentialStorageError;

  const saveBaseUrl = useCallback(
    async (baseUrl: string) => {
      try {
        const normalized = normalizeMongarsApiBaseUrl(baseUrl);
        const currentOrigin = configuration.baseUrl
          ? getMongarsApiOrigin(configuration.baseUrl)
          : null;
        const destinationChanged = currentOrigin !== getMongarsApiOrigin(normalized);
        if (destinationChanged) {
          // Never carry a bearer credential across origins. The user must explicitly authenticate
          // again after selecting a different control plane.
          await clearApiToken();
        }
        const saved = await saveApiBaseUrl(normalized);
        setBaseUrlStorageError(null);
        return saved;
      } catch (error) {
        const storageError =
          error instanceof Error ? error : new Error('Unable to save the server URL.');
        setBaseUrlStatus('error');
        setBaseUrlStorageError(storageError);
        throw storageError;
      }
    },
    [configuration.baseUrl],
  );

  const clearBaseUrl = useCallback(async () => {
    try {
      await clearApiToken();
      await clearApiBaseUrl();
      setBaseUrlStorageError(null);
    } catch (error) {
      const storageError =
        error instanceof Error ? error : new Error('Unable to clear the server URL.');
      setBaseUrlStatus('error');
      setBaseUrlStorageError(storageError);
      throw storageError;
    }
  }, []);

  const saveToken = useCallback(
    async (token: string) => {
      try {
        if (!configuration.transportSecurity?.canSendCredentials) {
          throw new ApiConfigurationError(
            configuration.transportSecurity?.message ??
              'Configure a secure monGARS API URL before saving a token.',
          );
        }
        await saveApiToken(configuration.baseUrl, token);
      } catch (error) {
        const storageError =
          error instanceof Error ? error : new Error('Unable to store the API token.');
        setCredentialStatus('error');
        setCredentialStorageError(storageError);
        throw storageError;
      }
    },
    [configuration.baseUrl, configuration.transportSecurity],
  );

  const clearToken = useCallback(async () => {
    try {
      await clearApiToken();
    } catch (error) {
      const storageError =
        error instanceof Error ? error : new Error('Unable to clear the API token.');
      setCredentialStorageError(storageError);
      throw storageError;
    }
  }, []);

  const value = useMemo<MongarsContextValue>(
    () => ({
      client: configuration.client,
      baseUrl: configuration.baseUrl,
      baseUrlStatus,
      baseUrlStorageError,
      configurationError: configuration.error,
      transportSecurity: configuration.transportSecurity,
      tokenStatus,
      hasToken: tokenStatus === 'ready',
      tokenStorageError,
      saveBaseUrl,
      clearBaseUrl,
      saveToken,
      clearToken,
    }),
    [
      configuration,
      baseUrlStatus,
      baseUrlStorageError,
      tokenStatus,
      tokenStorageError,
      saveBaseUrl,
      clearBaseUrl,
      saveToken,
      clearToken,
    ],
  );

  return <MongarsContext.Provider value={value}>{children}</MongarsContext.Provider>;
}

export function useMongars(): MongarsContextValue {
  const context = use(MongarsContext);
  if (!context) {
    throw new Error('useMongars must be used inside MongarsProvider.');
  }
  return context;
}

export function useMongarsClient(): MongarsClient {
  const { client, configurationError } = useMongars();
  if (!client) {
    throw configurationError ?? new ApiConfigurationError('The monGARS API is not configured.');
  }
  return client;
}
