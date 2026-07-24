'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function transpile(filename, customRequire) {
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      strict: true,
    },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  const execute = new Function('exports', 'require', 'module', '__filename', '__dirname', output);
  execute(module.exports, customRequire, module, filename, path.dirname(filename));
  return module.exports;
}

function modules() {
  const ndjsonFilename = path.join(__dirname, '..', 'lib', 'api', 'ndjson.ts');
  const ndjson = transpile(ndjsonFilename, require);
  class ApiConfigurationError extends Error {}
  const origin = {
    ApiConfigurationError,
    assertSecureCredentialTransport() {},
    getApiTransportSecurity() {
      return { secure: true };
    },
    getMongarsApiBaseUrl(value) {
      return (value ?? 'https://station.test').replace(/\/$/, '');
    },
    getMongarsApiOrigin(value) {
      return new URL(value).origin;
    },
    normalizeMongarsApiBaseUrl(value) {
      return value.replace(/\/$/, '');
    },
  };
  const defaultTokenStore = {
    async read() {
      return 'default-token';
    },
    async clear() {},
  };
  const customRequire = (identifier) => {
    if (identifier === 'expo/fetch') return { fetch: globalThis.fetch };
    if (identifier === '@/lib/api-token') return { apiTokenStore: defaultTokenStore };
    if (identifier === '@/lib/api-origin') return origin;
    if (identifier === '@/lib/api/ndjson') return ndjson;
    return require(identifier);
  };
  const clientFilename = path.join(__dirname, '..', 'lib', 'api', 'client.ts');
  return { ...ndjson, ...transpile(clientFilename, customRequire) };
}

const { ApiError, MongarsClient } = modules();

function streamingResponse(frames, splitAt = null) {
  const bytes = new TextEncoder().encode(`${frames.map(JSON.stringify).join('\n')}\n`);
  const chunks = splitAt === null ? [bytes] : [bytes.slice(0, splitAt), bytes.slice(splitAt)];
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    }),
    { status: 200, headers: { 'content-type': 'application/x-ndjson; charset=utf-8' } },
  );
}

function validFrames() {
  return [
    { type: 'start', trace_id: 'trc_mobile', session_id: 'session-mobile' },
    {
      type: 'sources',
      sources: [
        {
          key: 'M1',
          kind: 'memory',
          source_id: 'chunk-1',
          title: 'Manual',
          url: null,
          locator: { page: 7 },
          included: true,
        },
      ],
    },
    { type: 'delta', text: 'Grounded ' },
    { type: 'delta', text: 'answer [M1].' },
    {
      type: 'final',
      trace_id: 'trc_mobile',
      session_id: 'session-mobile',
      status: 'ok',
      answer: 'Grounded answer [M1].',
      model: 'qwen3:4b',
      memory_hits: 1,
      web_search_status: 'not_requested',
      sources: [],
      citations: [
        {
          key: 'M1',
          kind: 'memory',
          source_id: 'chunk-1',
          title: 'Manual',
          url: null,
          locator: { page: 7 },
        },
      ],
    },
  ];
}

function testClient(frames) {
  return new MongarsClient({
    baseUrl: 'https://station.test',
    fetcher: async () => streamingResponse(frames),
    tokenStore: { async read() { return 'token'; }, async clear() {} },
  });
}


test('streamChat authenticates, parses split frames, and returns only the final response', async () => {
  const requests = [];
  const tokenReads = [];
  const deltas = [];
  const sourceCatalogs = [];
  const fetcher = async (url, init) => {
    requests.push({ url, init });
    return streamingResponse(validFrames(), 37);
  };
  const client = new MongarsClient({
    baseUrl: 'https://station.test',
    fetcher,
    tokenStore: {
      async read(origin) {
        tokenReads.push(origin);
        return 'secret-token';
      },
      async clear() {},
    },
  });

  const response = await client.streamChat(
    { message: 'hello', require_local_only: true },
    {
      onDelta(text) {
        deltas.push(text);
      },
      onSources(sources) {
        sourceCatalogs.push(sources);
      },
    },
  );

  assert.deepEqual(tokenReads, ['https://station.test']);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://station.test/v1/chat/stream');
  assert.equal(requests[0].init.headers.get('Authorization'), 'Bearer secret-token');
  assert.equal(requests[0].init.headers.get('Accept'), 'application/x-ndjson');
  assert.deepEqual(deltas, ['Grounded ', 'answer [M1].']);
  assert.equal(sourceCatalogs[0][0].key, 'M1');
  assert.equal(response.answer, 'Grounded answer [M1].');
  assert.equal(response.citations[0].locator.page, 7);
});


test('streamChat rejects server error frames without accepting a final response', async () => {
  const frames = validFrames().slice(0, 3);
  frames.push({ type: 'error', code: 'inference_timeout', retryable: true });

  await assert.rejects(
    testClient(frames).streamChat({ message: 'hello' }),
    (error) => error instanceof ApiError && error.code === 'inference_timeout' && error.status === 503,
  );
});


test('streamChat rejects a final frame that changes the trace identity', async () => {
  const frames = validFrames();
  frames[frames.length - 1] = { ...frames[frames.length - 1], trace_id: 'trc_substituted' };

  await assert.rejects(
    testClient(frames).streamChat({ message: 'hello' }),
    (error) => error instanceof ApiError && error.code === 'STREAM_PROTOCOL_ERROR',
  );
});


test('streamChat rejects a final answer that differs from displayed deltas', async () => {
  const frames = validFrames();
  frames[frames.length - 1] = {
    ...frames[frames.length - 1],
    answer: 'Substituted final answer.',
  };

  await assert.rejects(
    testClient(frames).streamChat({ message: 'hello' }),
    (error) => error instanceof ApiError && error.code === 'STREAM_PROTOCOL_ERROR',
  );
});


test('streamChat rejects streams that exceed the total frame ceiling', async () => {
  const frames = [
    { type: 'start', trace_id: 'trc_many', session_id: 'session-many' },
    { type: 'sources', sources: [] },
    ...Array.from({ length: 10_000 }, () => ({ type: 'delta', text: 'x' })),
  ];

  await assert.rejects(
    testClient(frames).streamChat({ message: 'hello' }),
    (error) => error instanceof ApiError && error.code === 'STREAM_PROTOCOL_ERROR',
  );
});
