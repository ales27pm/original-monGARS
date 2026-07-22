export type ApiTransportSecurity = {
  kind: 'https' | 'loopback-http' | 'insecure-http';
  canSendCredentials: boolean;
  message: string;
};

export class ApiConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ApiConfigurationError';
  }
}

export function normalizeMongarsApiBaseUrl(configured: string): string {
  if (!configured.trim()) {
    throw new ApiConfigurationError('Enter the monGARS server URL.');
  }

  let parsed: URL;
  try {
    parsed = new URL(configured.trim());
  } catch {
    throw new ApiConfigurationError('The monGARS API URL must be a valid URL.');
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new ApiConfigurationError('The monGARS API URL must use HTTP or HTTPS.');
  }
  if (parsed.username || parsed.password) {
    throw new ApiConfigurationError('The monGARS API URL must not contain credentials.');
  }
  if (parsed.search || parsed.hash) {
    throw new ApiConfigurationError('The monGARS API URL must not contain a query or fragment.');
  }

  return parsed.toString().replace(/\/+$/, '');
}

/** Return the normalized security origin to which a bearer credential is bound. */
export function getMongarsApiOrigin(configured: string): string {
  return new URL(normalizeMongarsApiBaseUrl(configured)).origin;
}

/** Return whether a user-entered endpoint resolves to the exact active API base URL. */
export function isActiveMongarsApiBaseUrlDraft(
  draft: string,
  activeBaseUrl: string | null,
): boolean {
  if (!activeBaseUrl) return false;

  try {
    return normalizeMongarsApiBaseUrl(draft) === normalizeMongarsApiBaseUrl(activeBaseUrl);
  } catch {
    return false;
  }
}

export function getMongarsApiBaseUrl(override?: string): string {
  const configured = override ?? process.env.EXPO_PUBLIC_MONGARS_API_URL;
  if (!configured?.trim()) {
    throw new ApiConfigurationError(
      'No monGARS server URL is configured. Open Settings and enter an HTTPS server URL.',
    );
  }

  return normalizeMongarsApiBaseUrl(configured);
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (normalized === 'localhost' || normalized.endsWith('.localhost') || normalized === '::1') {
    return true;
  }

  const octets = normalized.split('.').map(Number);
  return (
    octets.length === 4 &&
    octets.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255) &&
    octets[0] === 127
  );
}

/** Classify whether a server URL is safe for bearer credentials. */
export function getApiTransportSecurity(baseUrl: string): ApiTransportSecurity {
  const parsed = new URL(getMongarsApiBaseUrl(baseUrl));
  if (parsed.protocol === 'https:') {
    return {
      kind: 'https',
      canSendCredentials: true,
      message: 'Bearer credentials are protected by HTTPS.',
    };
  }
  if (isLoopbackHostname(parsed.hostname)) {
    return {
      kind: 'loopback-http',
      canSendCredentials: true,
      message: 'Loopback HTTP is acceptable for same-device development only.',
    };
  }
  return {
    kind: 'insecure-http',
    canSendCredentials: false,
    message: 'Use HTTPS before sending a bearer token to a non-loopback server.',
  };
}

export function assertSecureCredentialTransport(baseUrl: string): void {
  const security = getApiTransportSecurity(baseUrl);
  if (!security.canSendCredentials) {
    throw new ApiConfigurationError(security.message);
  }
}
