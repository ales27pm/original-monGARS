import * as SecureStore from 'expo-secure-store';

import {
  ApiConfigurationError,
  getApiTransportSecurity,
  getMongarsApiOrigin,
} from '@/lib/api-origin';

const CREDENTIAL_KEY = 'mongars.api-credential.v1';
const LEGACY_TOKEN_KEY = 'mongars.api-token.v1';
const SECURE_STORE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export type StoredCredential = Readonly<{
  origin: string;
  token: string;
  version: 1;
}>;

type CredentialListener = (credential: StoredCredential | null) => void;

let cachedCredential: StoredCredential | null = null;
let credentialLoaded = false;
let loadPromise: Promise<StoredCredential | null> | null = null;
const listeners = new Set<CredentialListener>();

async function secureStoreAvailable(): Promise<boolean> {
  if (process.env.EXPO_OS === 'web') {
    return false;
  }

  return SecureStore.isAvailableAsync();
}

function publish(credential: StoredCredential | null): void {
  listeners.forEach((listener) => listener(credential));
}

function parseCredential(raw: string): StoredCredential {
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    throw new Error('The saved monGARS credential is invalid. Authenticate again.');
  }

  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    (value as { version?: unknown }).version !== 1 ||
    typeof (value as { origin?: unknown }).origin !== 'string' ||
    typeof (value as { token?: unknown }).token !== 'string'
  ) {
    throw new Error('The saved monGARS credential is invalid. Authenticate again.');
  }

  const origin = getMongarsApiOrigin((value as { origin: string }).origin);
  const token = (value as { token: string }).token.trim();
  if (!token || origin !== (value as { origin: string }).origin) {
    throw new Error('The saved monGARS credential is invalid. Authenticate again.');
  }
  if (!getApiTransportSecurity(origin).canSendCredentials) {
    throw new Error('The saved monGARS credential uses an unsafe server origin. Authenticate again.');
  }

  return { origin, token, version: 1 };
}

/** Read the atomically stored bearer credential and its bound server origin. */
export async function readApiCredential(): Promise<StoredCredential | null> {
  if (credentialLoaded) {
    return cachedCredential;
  }

  if (loadPromise) {
    return loadPromise;
  }

  loadPromise = (async () => {
    if (await secureStoreAvailable()) {
      const raw = await SecureStore.getItemAsync(CREDENTIAL_KEY, SECURE_STORE_OPTIONS);
      cachedCredential = raw ? parseCredential(raw) : null;

      // Unbound v1 tokens from older releases are deliberately not migrated. They have no trusted
      // origin and must never be paired with a newly inlined build-time URL.
      await SecureStore.deleteItemAsync(LEGACY_TOKEN_KEY, SECURE_STORE_OPTIONS);
    }
    credentialLoaded = true;
    publish(cachedCredential);
    return cachedCredential;
  })().finally(() => {
    loadPromise = null;
  });

  return loadPromise;
}

/** Read a token only when it belongs to the exact active security origin. */
export async function readApiToken(expectedOrigin: string): Promise<string | null> {
  const credential = await readApiCredential();
  if (!credential) {
    return null;
  }

  const normalizedExpectedOrigin = getMongarsApiOrigin(expectedOrigin);
  if (credential.origin !== normalizedExpectedOrigin) {
    throw new ApiConfigurationError(
      'The saved token belongs to another monGARS server. Authenticate again.',
    );
  }
  return credential.token;
}

/** Store origin and token together under one SecureStore key. */
export async function saveApiToken(origin: string, token: string): Promise<void> {
  const normalizedOrigin = getMongarsApiOrigin(origin);
  if (!getApiTransportSecurity(normalizedOrigin).canSendCredentials) {
    throw new ApiConfigurationError('Use HTTPS before saving a bearer token for this server.');
  }

  const normalizedToken = token.trim();
  if (!normalizedToken) {
    throw new Error('API token cannot be empty.');
  }

  const credential: StoredCredential = {
    origin: normalizedOrigin,
    token: normalizedToken,
    version: 1,
  };
  if (await secureStoreAvailable()) {
    await SecureStore.setItemAsync(
      CREDENTIAL_KEY,
      JSON.stringify(credential),
      SECURE_STORE_OPTIONS,
    );
    await SecureStore.deleteItemAsync(LEGACY_TOKEN_KEY, SECURE_STORE_OPTIONS);
  }

  cachedCredential = credential;
  credentialLoaded = true;
  publish(cachedCredential);
}

export async function clearApiToken(): Promise<void> {
  // Invalidate the in-process credential first. A native storage failure must not keep sending a
  // credential that the server has rejected.
  cachedCredential = null;
  credentialLoaded = true;
  publish(null);

  if (await secureStoreAvailable()) {
    await SecureStore.deleteItemAsync(CREDENTIAL_KEY, SECURE_STORE_OPTIONS);
    await SecureStore.deleteItemAsync(LEGACY_TOKEN_KEY, SECURE_STORE_OPTIONS);
  }
}

export function subscribeApiCredential(listener: CredentialListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export type ApiTokenStore = {
  read: (expectedOrigin: string) => Promise<string | null>;
  clear: () => Promise<void>;
};

export const apiTokenStore: ApiTokenStore = {
  read: readApiToken,
  clear: clearApiToken,
};
