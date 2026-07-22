import * as SecureStore from 'expo-secure-store';

import {
  ApiConfigurationError,
  getApiTransportSecurity,
  normalizeMongarsApiBaseUrl,
} from '@/lib/api/client';

const BASE_URL_KEY = 'mongars.api-base-url.v1';
const SECURE_STORE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

type BaseUrlListener = (baseUrl: string | null) => void;

let cachedBaseUrl: string | null = null;
let baseUrlLoaded = false;
let loadPromise: Promise<string | null> | null = null;
const listeners = new Set<BaseUrlListener>();

async function secureStoreAvailable(): Promise<boolean> {
  if (process.env.EXPO_OS === 'web') {
    return false;
  }

  return SecureStore.isAvailableAsync();
}

function publish(baseUrl: string | null): void {
  listeners.forEach((listener) => listener(baseUrl));
}

/** Read the user-selected monGARS server URL from native device storage. */
export async function readApiBaseUrl(): Promise<string | null> {
  if (baseUrlLoaded) {
    return cachedBaseUrl;
  }

  if (loadPromise) {
    return loadPromise;
  }

  loadPromise = (async () => {
    if (await secureStoreAvailable()) {
      cachedBaseUrl = await SecureStore.getItemAsync(BASE_URL_KEY, SECURE_STORE_OPTIONS);
    }
    baseUrlLoaded = true;
    publish(cachedBaseUrl);
    return cachedBaseUrl;
  })().finally(() => {
    loadPromise = null;
  });

  return loadPromise;
}

/** Save only endpoints that are safe destinations for bearer credentials. */
export async function saveApiBaseUrl(baseUrl: string): Promise<string> {
  const normalized = normalizeMongarsApiBaseUrl(baseUrl);
  const transport = getApiTransportSecurity(normalized);
  if (!transport.canSendCredentials) {
    throw new ApiConfigurationError(transport.message);
  }

  if (await secureStoreAvailable()) {
    await SecureStore.setItemAsync(BASE_URL_KEY, normalized, SECURE_STORE_OPTIONS);
  }

  cachedBaseUrl = normalized;
  baseUrlLoaded = true;
  publish(cachedBaseUrl);
  return normalized;
}

export async function clearApiBaseUrl(): Promise<void> {
  cachedBaseUrl = null;
  baseUrlLoaded = true;
  publish(null);

  if (await secureStoreAvailable()) {
    await SecureStore.deleteItemAsync(BASE_URL_KEY, SECURE_STORE_OPTIONS);
  }
}

export function subscribeApiBaseUrl(listener: BaseUrlListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
