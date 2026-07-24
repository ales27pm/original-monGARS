import type { ChatCitation, ChatResponse, ChatStreamFrame, WebSource } from '@/types/mongars-api';

const MAX_LINE_BYTES = 1_000_000;
const textEncoder = new TextEncoder();
const WEB_SEARCH_STATUSES = new Set([
  'not_requested',
  'ok',
  'disabled',
  'unavailable',
  'no_results',
  'context_limited',
]);
const CITATION_KINDS = new Set(['memory', 'web', 'conversation', 'policy']);

export class NdjsonChatDecoder {
  private buffer = '';

  push(chunk: string): ChatStreamFrame[] {
    this.buffer += chunk;
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() ?? '';
    this.assertBufferBounded();
    return lines.flatMap((line) => this.parseLine(line));
  }

  finish(): ChatStreamFrame[] {
    const trailing = this.buffer;
    this.buffer = '';
    return this.parseLine(trailing);
  }

  private parseLine(line: string): ChatStreamFrame[] {
    const normalized = line.endsWith('\r') ? line.slice(0, -1) : line;
    if (!normalized.trim()) return [];
    if (textEncoder.encode(normalized).byteLength > MAX_LINE_BYTES) {
      throw new Error('The monGARS stream frame exceeded its byte limit.');
    }
    let value: unknown;
    try {
      value = JSON.parse(normalized) as unknown;
    } catch {
      throw new Error('The monGARS stream returned invalid NDJSON.');
    }
    return [parseChatStreamFrame(value)];
  }

  private assertBufferBounded(): void {
    if (textEncoder.encode(this.buffer).byteLength > MAX_LINE_BYTES) {
      throw new Error('The monGARS stream frame exceeded its byte limit.');
    }
  }
}

export function parseChatStreamFrame(value: unknown): ChatStreamFrame {
  if (!isRecord(value) || typeof value.type !== 'string') {
    throw new Error('The monGARS stream returned an invalid frame.');
  }
  switch (value.type) {
    case 'start':
      if (
        value.protocol !== 'mongars-chat-ndjson-v1' ||
        typeof value.stream_id !== 'string' ||
        !/^str_[0-9a-f]{32}$/.test(value.stream_id)
      ) {
        throw new Error('The monGARS stream returned an invalid start frame.');
      }
      return value as ChatStreamFrame;
    case 'attempt':
      if (!positiveInteger(value.attempt)) {
        throw new Error('The monGARS stream returned an invalid attempt frame.');
      }
      return value as ChatStreamFrame;
    case 'reset':
      if (!positiveInteger(value.attempt) || value.reason !== 'validation_retry') {
        throw new Error('The monGARS stream returned an invalid reset frame.');
      }
      return value as ChatStreamFrame;
    case 'delta':
      if (!positiveInteger(value.attempt) || typeof value.text !== 'string' || !value.text) {
        throw new Error('The monGARS stream returned an invalid delta frame.');
      }
      return value as ChatStreamFrame;
    case 'final':
      if (!isChatResponse(value.response)) {
        throw new Error('The monGARS stream returned an invalid final frame.');
      }
      return value as ChatStreamFrame;
    case 'error':
      if (
        typeof value.code !== 'string' ||
        !value.code ||
        typeof value.retryable !== 'boolean' ||
        value.discard_partial !== true
      ) {
        throw new Error('The monGARS stream returned an invalid error frame.');
      }
      return value as ChatStreamFrame;
    default:
      throw new Error('The monGARS stream returned an unsupported frame.');
  }
}

function isChatResponse(value: unknown): value is ChatResponse {
  return (
    isRecord(value) &&
    nonEmptyString(value.trace_id) &&
    nonEmptyString(value.session_id) &&
    value.status === 'ok' &&
    typeof value.answer === 'string' &&
    nonEmptyString(value.model) &&
    nonNegativeInteger(value.memory_hits) &&
    typeof value.web_search_status === 'string' &&
    WEB_SEARCH_STATUSES.has(value.web_search_status) &&
    (!('citations' in value) ||
      (Array.isArray(value.citations) && value.citations.every(isChatCitation))) &&
    (!('sources' in value) ||
      (Array.isArray(value.sources) && value.sources.every(isWebSource)))
  );
}

function isChatCitation(value: unknown): value is ChatCitation {
  return (
    isRecord(value) &&
    nonEmptyString(value.key) &&
    typeof value.kind === 'string' &&
    CITATION_KINDS.has(value.kind) &&
    nullableString(value.source_id) &&
    nullableString(value.title) &&
    nullableString(value.url) &&
    (value.locator === null || isRecord(value.locator))
  );
}

function isWebSource(value: unknown): value is WebSource {
  return isRecord(value) && typeof value.title === 'string' && nonEmptyString(value.url);
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === 'number' && value > 0;
}

function nonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === 'number' && value >= 0;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim());
}

function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
