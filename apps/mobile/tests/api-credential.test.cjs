const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');

function loadTypeScriptModule(relativePath, imports) {
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

function loadCredentialStore(initial = {}, overrides = {}) {
  const values = new Map(Object.entries(initial));
  const writes = [];
  const deletes = [];
  const secureStore = {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
    deleteItemAsync: async (key) => {
      deletes.push(key);
      values.delete(key);
    },
    getItemAsync: async (key) => {
      if (overrides.readError) throw overrides.readError;
      return values.get(key) ?? null;
    },
    isAvailableAsync: async () => true,
    setItemAsync: async (key, value, options) => {
      writes.push({ key, value, options });
      values.set(key, value);
    },
  };
  const origin = loadTypeScriptModule('lib/api-origin.ts', {});
  const store = loadTypeScriptModule('lib/api-token.ts', {
    'expo-secure-store': secureStore,
    '@/lib/api-origin': origin,
  });
  return { deletes, origin, store, values, writes };
}

function loadClient(defaultTokenStore) {
  const origin = loadTypeScriptModule('lib/api-origin.ts', {});
  return loadTypeScriptModule('lib/api/client.ts', {
    'expo/fetch': { fetch: () => Promise.reject(new Error('Unexpected default fetch.')) },
    '@/lib/api-origin': origin,
    '@/lib/api-token': { apiTokenStore: defaultTokenStore },
  });
}

test.beforeEach(() => {
  process.env.EXPO_OS = 'ios';
});

test.afterEach(() => {
  delete process.env.EXPO_OS;
  delete process.env.EXPO_PUBLIC_MONGARS_API_URL;
});

test('credential is stored atomically with a normalized security origin', async () => {
  const { store, writes } = loadCredentialStore();

  await store.saveApiToken('https://Control.Example.test:443/control///', '  bearer-value  ');

  assert.equal(writes.length, 1);
  assert.equal(writes[0].key, 'mongars.api-credential.v1');
  assert.deepEqual(JSON.parse(writes[0].value), {
    origin: 'https://control.example.test',
    token: 'bearer-value',
    version: 1,
  });
  assert.equal(
    await store.readApiToken('https://control.example.test/another/api/path'),
    'bearer-value',
  );
});

test('credential cannot be reused by a build-time URL on another origin', async () => {
  const credential = JSON.stringify({
    origin: 'https://old-build.example.test',
    token: 'old-token',
    version: 1,
  });
  const { store } = loadCredentialStore({ 'mongars.api-credential.v1': credential });

  await assert.rejects(
    () => store.readApiToken('https://new-build.example.test'),
    /saved token belongs to another monGARS server/,
  );
});

test('a changed build-time URL is rejected before any authenticated network request', async () => {
  const credential = JSON.stringify({
    origin: 'https://old-build.example.test',
    token: 'old-token',
    version: 1,
  });
  const { store } = loadCredentialStore({ 'mongars.api-credential.v1': credential });
  const { MongarsClient } = loadClient(store.apiTokenStore);
  process.env.EXPO_PUBLIC_MONGARS_API_URL = 'https://new-build.example.test';
  let fetchCalls = 0;
  const client = new MongarsClient({
    fetcher: async () => {
      fetchCalls += 1;
      return new Response('{}', { status: 200 });
    },
    tokenStore: store.apiTokenStore,
  });

  await assert.rejects(
    () => client.chat({ message: 'private content', web_search: 'off' }),
    /saved token belongs to another monGARS server/,
  );
  assert.equal(fetchCalls, 0);
});

test('runtime overrides cannot receive a credential belonging to another server', async () => {
  const credential = JSON.stringify({
    origin: 'https://configured.example.test',
    token: 'configured-token',
    version: 1,
  });
  const { store } = loadCredentialStore({ 'mongars.api-credential.v1': credential });

  await assert.rejects(
    () => store.readApiToken('https://preview-override.example.test'),
    /Authenticate again/,
  );
});

test('an unbound legacy token is deleted rather than migrated during an upgrade', async () => {
  const { deletes, store, values } = loadCredentialStore({
    'mongars.api-token.v1': 'legacy-unbound-token',
  });

  assert.equal(await store.readApiCredential(), null);
  assert.equal(values.has('mongars.api-token.v1'), false);
  assert.ok(deletes.includes('mongars.api-token.v1'));
});

test('credential storage read failures fail closed', async () => {
  const { store } = loadCredentialStore({}, { readError: new Error('Keychain unavailable') });

  await assert.rejects(() => store.readApiCredential(), /Keychain unavailable/);
  await assert.rejects(
    () => store.readApiToken('https://control.example.test'),
    /Keychain unavailable/,
  );
});

test('malformed saved credentials never release a bearer token', async () => {
  const { store } = loadCredentialStore({
    'mongars.api-credential.v1': JSON.stringify({
      origin: 'https://control.example.test/path',
      token: 'must-not-send',
      version: 1,
    }),
  });

  await assert.rejects(
    () => store.readApiToken('https://control.example.test'),
    /saved monGARS credential is invalid/,
  );
});

test('readiness sends the origin-bound bearer token while liveness remains public', async () => {
  const requests = [];
  let tokenReads = 0;
  const tokenStore = {
    clear: async () => undefined,
    read: async (origin) => {
      tokenReads += 1;
      assert.equal(origin, 'https://control.example.test');
      return 'readiness-token';
    },
    save: async () => undefined,
    subscribe: () => () => undefined,
  };
  const { MongarsClient } = loadClient(tokenStore);
  const client = new MongarsClient({
    baseUrl: 'https://control.example.test',
    fetcher: async (url, init = {}) => {
      requests.push({ url, headers: new Headers(init.headers) });
      return new Response('{"status":"ok"}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    },
    tokenStore,
  });

  await client.health();
  await client.readiness();

  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, 'https://control.example.test/v1/healthz');
  assert.equal(requests[0].headers.get('Authorization'), null);
  assert.equal(requests[1].url, 'https://control.example.test/v1/readyz');
  assert.equal(requests[1].headers.get('Authorization'), 'Bearer readiness-token');
  assert.equal(tokenReads, 1);
});
