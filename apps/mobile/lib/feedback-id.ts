const UUID_BYTES = 16;

export function createFeedbackId(): string {
  if (
    globalThis.crypto === undefined
    || typeof globalThis.crypto.getRandomValues !== 'function'
  ) {
    throw new Error('Secure random UUID generation is unavailable on this device.');
  }

  const bytes = new Uint8Array(UUID_BYTES);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;

  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'));
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10, 16).join(''),
  ].join('-');
}
