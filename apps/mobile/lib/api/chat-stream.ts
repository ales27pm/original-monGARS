import type { ChatResponse, ChatStreamFrame } from '@/types/mongars-api';

const MAX_LINE_BYTES = 1_000_000;
const textEncoder = new TextEncoder();

export class NdjsonChatDecoder {
  private buffer = '';

  push(chunk: string): ChatStreamFrame[] {
    this.buffer += chunk;
    this.assertBufferBounded();
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() ?? '';
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
    typeof value.trace_id === 'string' &&
    typeof value.session_id === 'string' &&
    value.status === 'ok' &&
    typeof value.answer === 'string' &&
    typeof value.model === 'string' &&
    Number.isSafeInteger(value.memory_hits) &&
    typeof value.web_search_status === 'string' &&
    (!('citations' in value) || Array.isArray(value.citations)) &&
    (!('sources' in value) || Array.isArray(value.sources))
  );
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === 'number' && value > 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
