import type {
  ChatCitation,
  ChatResponse,
  ChatStreamFrame,
  ChatStreamSource,
  JsonValue,
  WebSource,
} from '@/types/mongars-api';

const MAX_LINE_BYTES = 1_000_000;
const MAX_BUFFER_CHARACTERS = 1_100_000;
const MAX_SOURCES = 1_000;
const MAX_JSON_DEPTH = 24;
const EVIDENCE_KEY = /^[HMWP][1-9][0-9]{0,2}$/;
const ERROR_CODE = /^[a-z0-9_]{1,100}$/;
const WEB_SEARCH_STATUSES = new Set([
  'not_requested',
  'ok',
  'disabled',
  'unavailable',
  'no_results',
  'context_limited',
]);
const EVIDENCE_KINDS = new Set(['memory', 'web', 'conversation', 'policy']);

export class ChatStreamProtocolError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ChatStreamProtocolError';
  }
}

export class ChatNdjsonDecoder {
  private readonly decoder = new TextDecoder('utf-8', { fatal: true });
  private buffer = '';

  push(chunk: Uint8Array): ChatStreamFrame[] {
    if (!(chunk instanceof Uint8Array)) {
      throw new ChatStreamProtocolError('The chat stream emitted a non-byte chunk.');
    }
    this.buffer += this.decoder.decode(chunk, { stream: true });
    return this.drain(false);
  }

  finish(): ChatStreamFrame[] {
    this.buffer += this.decoder.decode();
    return this.drain(true);
  }

  private drain(final: boolean): ChatStreamFrame[] {
    const frames: ChatStreamFrame[] = [];
    let newline = this.buffer.indexOf('\n');
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, '');
      this.buffer = this.buffer.slice(newline + 1);
      if (line.trim()) frames.push(parseChatStreamFrameLine(line));
      newline = this.buffer.indexOf('\n');
    }

    if (this.buffer.length > MAX_BUFFER_CHARACTERS) {
      throw new ChatStreamProtocolError('The chat stream line exceeded its buffer ceiling.');
    }
    if (final && this.buffer.trim()) {
      const line = this.buffer.replace(/\r$/, '');
      this.buffer = '';
      frames.push(parseChatStreamFrameLine(line));
    } else if (final) {
      this.buffer = '';
    }
    return frames;
  }
}

export function parseChatStreamFrameLine(line: string): ChatStreamFrame {
  if (typeof line !== 'string' || !line.trim()) {
    throw new ChatStreamProtocolError('The chat stream emitted an empty frame.');
  }
  if (new TextEncoder().encode(line).byteLength > MAX_LINE_BYTES) {
    throw new ChatStreamProtocolError('The chat stream frame exceeded its byte ceiling.');
  }

  let value: unknown;
  try {
    value = JSON.parse(line) as unknown;
  } catch (error) {
    throw new ChatStreamProtocolError('The chat stream emitted invalid JSON.', { cause: error });
  }
  if (!isRecord(value) || typeof value.type !== 'string') {
    throw new ChatStreamProtocolError('The chat stream frame has no valid type.');
  }

  switch (value.type) {
    case 'start':
      return {
        type: 'start',
        trace_id: boundedString(value.trace_id, 'trace_id', 128),
        session_id: boundedString(value.session_id, 'session_id', 64),
      };
    case 'sources':
      return {
        type: 'sources',
        sources: sourceArray(value.sources, true),
      };
    case 'delta':
      return {
        type: 'delta',
        text: boundedString(value.text, 'delta text', MAX_LINE_BYTES),
      };
    case 'final':
      return {
        type: 'final',
        ...chatResponse(value),
        sources: webSourceArray(value.sources),
        citations: sourceArray(value.citations, false),
      };
    case 'error':
      if (typeof value.code !== 'string' || !ERROR_CODE.test(value.code)) {
        throw new ChatStreamProtocolError('The chat stream error code is invalid.');
      }
      if (typeof value.retryable !== 'boolean') {
        throw new ChatStreamProtocolError('The chat stream retryable flag is invalid.');
      }
      return { type: 'error', code: value.code, retryable: value.retryable };
    default:
      throw new ChatStreamProtocolError(`Unsupported chat stream frame: ${value.type}.`);
  }
}

function chatResponse(value: Record<string, unknown>): ChatResponse {
  if (value.status !== 'ok') {
    throw new ChatStreamProtocolError('The final chat frame has an invalid status.');
  }
  const memoryHits = value.memory_hits;
  if (!Number.isSafeInteger(memoryHits) || Number(memoryHits) < 0) {
    throw new ChatStreamProtocolError('The final chat frame has invalid memory metadata.');
  }
  if (
    typeof value.web_search_status !== 'string' ||
    !WEB_SEARCH_STATUSES.has(value.web_search_status)
  ) {
    throw new ChatStreamProtocolError('The final chat frame has an invalid web-search status.');
  }

  return {
    trace_id: boundedString(value.trace_id, 'trace_id', 128),
    session_id: boundedString(value.session_id, 'session_id', 64),
    status: 'ok',
    answer: boundedString(value.answer, 'answer', MAX_LINE_BYTES),
    model: boundedString(value.model, 'model', 255),
    memory_hits: Number(memoryHits),
    web_search_status: value.web_search_status as ChatResponse['web_search_status'],
  };
}

function sourceArray(value: unknown, includeFlag: true): ChatStreamSource[];
function sourceArray(value: unknown, includeFlag: false): ChatCitation[];
function sourceArray(
  value: unknown,
  includeFlag: boolean,
): ChatStreamSource[] | ChatCitation[] {
  if (!Array.isArray(value) || value.length > MAX_SOURCES) {
    throw new ChatStreamProtocolError('The chat stream source list is invalid.');
  }
  return value.map((item) => source(item, includeFlag));
}

function source(value: unknown, includeFlag: boolean): ChatStreamSource | ChatCitation {
  if (!isRecord(value)) {
    throw new ChatStreamProtocolError('A chat stream source is not an object.');
  }
  const key = boundedString(value.key, 'source key', 16);
  const kind = boundedString(value.kind, 'source kind', 20);
  if (!EVIDENCE_KEY.test(key) || !EVIDENCE_KINDS.has(kind)) {
    throw new ChatStreamProtocolError('A chat stream source has an invalid identity.');
  }
  const expectedPrefix = { conversation: 'H', memory: 'M', web: 'W', policy: 'P' }[kind];
  if (key[0] !== expectedPrefix) {
    throw new ChatStreamProtocolError('A chat stream source key does not match its kind.');
  }
  const locator = nullableJsonMapping(value.locator);
  const base: ChatCitation = {
    key,
    kind: kind as ChatCitation['kind'],
    source_id: nullableString(value.source_id, 'source_id', 255),
    title: nullableString(value.title, 'source title', 4_096),
    url: nullableString(value.url, 'source URL', 8_192),
    locator,
  };
  if (!includeFlag) return base;
  if (typeof value.included !== 'boolean') {
    throw new ChatStreamProtocolError('A chat stream source has an invalid included flag.');
  }
  return { ...base, included: value.included };
}

function webSourceArray(value: unknown): WebSource[] {
  if (!Array.isArray(value) || value.length > MAX_SOURCES) {
    throw new ChatStreamProtocolError('The final web-source list is invalid.');
  }
  return value.map((item) => {
    if (!isRecord(item)) {
      throw new ChatStreamProtocolError('A final web source is not an object.');
    }
    return {
      title: boundedString(item.title, 'web source title', 4_096),
      url: boundedString(item.url, 'web source URL', 8_192),
    };
  });
}

function nullableJsonMapping(value: unknown): { [key: string]: JsonValue } | null {
  if (value === null || value === undefined) return null;
  if (!isRecord(value) || !isJsonValue(value, 0)) {
    throw new ChatStreamProtocolError('A chat stream source locator is invalid.');
  }
  return value as { [key: string]: JsonValue };
}

function isJsonValue(value: unknown, depth: number): value is JsonValue {
  if (depth > MAX_JSON_DEPTH) return false;
  if (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'boolean' ||
    (typeof value === 'number' && Number.isFinite(value))
  ) {
    return true;
  }
  if (Array.isArray(value)) {
    return value.every((item) => isJsonValue(item, depth + 1));
  }
  if (isRecord(value)) {
    return Object.entries(value).every(
      ([key, item]) => key.length <= 255 && isJsonValue(item, depth + 1),
    );
  }
  return false;
}

function nullableString(value: unknown, field: string, maximum: number): string | null {
  if (value === null || value === undefined) return null;
  return boundedString(value, field, maximum);
}

function boundedString(value: unknown, field: string, maximum: number): string {
  if (typeof value !== 'string' || !value || value.length > maximum) {
    throw new ChatStreamProtocolError(`The chat stream ${field} is invalid.`);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
