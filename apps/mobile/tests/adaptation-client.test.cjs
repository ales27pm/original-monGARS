'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');

function loadTypeScriptModule(relativePath, imports = {}) {
  const filename = path.join(mobileRoot, relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: filename,
    reportDiagnostics: true,
  });
  assert.equal(output.diagnostics?.length ?? 0, 0);

  const loadedModule = { exports: {} };
  const requireFromTest = (specifier) => {
    if (Object.hasOwn(imports, specifier)) return imports[specifier];
    throw new Error(`Unexpected import from ${relativePath}: ${specifier}`);
  };
  const evaluate = new Function('require', 'module', 'exports', output.outputText);
  evaluate(requireFromTest, loadedModule, loadedModule.exports);
  return loadedModule.exports;
}

class ApiError extends Error {
  constructor(message, options) {
    super(message, { cause: options.cause });
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.detail = options.detail;
  }
}

function loadAdaptationClient(tokenStore, fetcher) {
  const origin = {
    assertSecureCredentialTransport: () => undefined,
    getMongarsApiOrigin: (value) => new URL(value).origin,
    normalizeMongarsApiBaseUrl: (value) => value.replace(/\/+$/, ''),
  };
  const client = loadTypeScriptModule('lib/api/adaptation.ts', {
    'expo/fetch': { fetch: fetcher },
    '@/lib/api-origin': origin,
    '@/lib/api-token': { apiTokenStore: tokenStore },
    '@/lib/api/client': {
      ApiError,
      isAbortError: (error) => error instanceof Error && error.name === 'AbortError',
    },
  });
  return new client.AdaptationClient({
    baseUrl: 'https://control.example.test',
    fetcher,
    tokenStore,
  });
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function tokenStore(overrides = {}) {
  return {
    clear: overrides.clear ?? (async () => undefined),
    read: overrides.read ?? (async () => 'feedback-token'),
    save: async () => undefined,
    subscribe: () => () => undefined,
  };
}

test('creates cryptographically shaped UUIDv4 feedback identifiers', () => {
  const { createFeedbackId } = loadTypeScriptModule('lib/feedback-id.ts');
  const first = createFeedbackId();
  const second = createFeedbackId();

  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.notEqual(first, second);
});

test('submits exact typed feedback with an origin-bound bearer credential', async () => {
  const requests = [];
  const store = tokenStore({
    read: async (origin) => {
      assert.equal(origin, 'https://control.example.test');
      return 'feedback-token';
    },
  });
  const client = loadAdaptationClient(store, async (url, init = {}) => {
    requests.push({ url, init });
    return jsonResponse({
      feedback_id: '8bfef8df-2e2b-4b93-8e2e-4cf067470300',
      kind: 'helpfulness',
      feedback_digest: 'a'.repeat(64),
      created: true,
      applied_task_id: null,
      applied_revision: null,
      proposal: null,
      profile: {
        revision: 0,
        source: 'default',
        profile_digest: null,
        schema_version: 'personality-v1',
        preferences: [],
      },
      proposal_task: null,
    });
  });

  await client.submitFeedback({
    kind: 'helpfulness',
    feedback_id: '8bfef8df-2e2b-4b93-8e2e-4cf067470300',
    response_trace_id: `trc_${'a'.repeat(32)}`,
    helpful: true,
  });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://control.example.test/v1/adaptation/feedback');
  assert.equal(requests[0].init.method, 'POST');
  const headers = new Headers(requests[0].init.headers);
  assert.equal(headers.get('Authorization'), 'Bearer feedback-token');
  assert.deepEqual(JSON.parse(requests[0].init.body), {
    kind: 'helpfulness',
    feedback_id: '8bfef8df-2e2b-4b93-8e2e-4cf067470300',
    response_trace_id: `trc_${'a'.repeat(32)}`,
    helpful: true,
  });
});

test('uses the reviewed personality profile endpoints and HTTP methods', async () => {
  const requests = [];
  const client = loadAdaptationClient(tokenStore(), async (url, init = {}) => {
    requests.push({ url, method: init.method ?? 'GET' });
    if (url.endsWith('/personality')) return new Response(null, { status: 204 });
    if (url.endsWith('/personality/export')) {
      return jsonResponse({
        exported_at: '2026-07-24T00:00:00Z',
        current: {
          revision: 0,
          source: 'default',
          profile_digest: null,
          schema_version: 'personality-v1',
          preferences: [],
        },
        history: [],
      });
    }
    if (url.includes('/profile/revisions')) return jsonResponse([]);
    return jsonResponse({
      revision: 0,
      source: 'default',
      profile_digest: null,
      schema_version: 'personality-v1',
      preferences: [],
    });
  });

  await client.getProfile();
  await client.getRevisions(500);
  await client.exportProfile();
  await client.resetProfile();
  await client.deleteProfile();

  assert.deepEqual(requests, [
    { url: 'https://control.example.test/v1/adaptation/profile', method: 'GET' },
    {
      url: 'https://control.example.test/v1/adaptation/profile/revisions?limit=100',
      method: 'GET',
    },
    { url: 'https://control.example.test/v1/adaptation/personality/export', method: 'GET' },
    { url: 'https://control.example.test/v1/adaptation/personality/reset', method: 'POST' },
    { url: 'https://control.example.test/v1/adaptation/personality', method: 'DELETE' },
  ]);
});

test('clears a rejected credential after an adaptation 401 response', async () => {
  let clearCount = 0;
  const store = tokenStore({
    clear: async () => {
      clearCount += 1;
    },
  });
  const client = loadAdaptationClient(store, async () =>
    jsonResponse({ detail: 'invalid bearer credential' }, 401),
  );

  await assert.rejects(() => client.getProfile(), (error) => {
    assert.equal(error.code, 'UNAUTHORIZED');
    assert.equal(error.status, 401);
    return true;
  });
  assert.equal(clearCount, 1);
});
