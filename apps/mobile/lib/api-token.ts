import * as SecureStore from 'expo-secure-store';

const TOKEN_KEY = 'mongars.api-token.v1';
const SECURE_STORE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

type TokenListener = (token: string | null) => void;

let cachedToken: string | null = null;
let tokenLoaded = false;
let loadPromise: Promise<string | null> | null = null;
const listeners = new Set<TokenListener>();

async function secureStoreAvailable(): Promise<boolean> {
  if (process.env.EXPO_OS === 'web') {
    return false;
  }

  return SecureStore.isAvailableAsync();
}

function publish(token: string | null): void {
  listeners.forEach((listener) => listener(token));
}

/**
 * Read the user-entered bearer token.
 *
 * Native builds use the iOS Keychain or Android Keystore through SecureStore. The web preview keeps
 * the token in memory only so a browser build never persists it in an insecure storage API.
 */
export async function readApiToken(): Promise<string | null> {
  if (tokenLoaded) {
    return cachedToken;
  }

  if (loadPromise) {
    return loadPromise;
  }

  loadPromise = (async () => {
    if (await secureStoreAvailable()) {
      cachedToken = await SecureStore.getItemAsync(TOKEN_KEY, SECURE_STORE_OPTIONS);
    }
    tokenLoaded = true;
    publish(cachedToken);
    return cachedToken;
  })().finally(() => {
    loadPromise = null;
  });

  return loadPromise;
}

export async function saveApiToken(token: string): Promise<void> {
  const normalized = token.trim();
  if (!normalized) {
    throw new Error('API token cannot be empty.');
  }

  if (await secureStoreAvailable()) {
    await SecureStore.setItemAsync(TOKEN_KEY, normalized, SECURE_STORE_OPTIONS);
  }

  cachedToken = normalized;
  tokenLoaded = true;
  publish(cachedToken);
}

export async function clearApiToken(): Promise<void> {
  // Invalidate the in-process credential first. A native storage failure must not keep sending a
  // credential that the server has rejected.
  cachedToken = null;
  tokenLoaded = true;
  publish(null);

  if (await secureStoreAvailable()) {
    await SecureStore.deleteItemAsync(TOKEN_KEY, SECURE_STORE_OPTIONS);
  }
}

export function subscribeApiToken(listener: TokenListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export type ApiTokenStore = {
  read: () => Promise<string | null>;
  clear: () => Promise<void>;
};

export const apiTokenStore: ApiTokenStore = {
  read: readApiToken,
  clear: clearApiToken,
};
