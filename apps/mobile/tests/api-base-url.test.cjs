const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');
const configuredUrlKey = 'EXPO_PUBLIC_MONGARS_API_URL';

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

  const diagnostics = output.diagnostics ?? [];
  assert.equal(
    diagnostics.length,
    0,
    diagnostics.map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')).join('\n'),
  );

  const loadedModule = { exports: {} };
  const requireFromTest = (specifier) => {
    if (Object.hasOwn(imports, specifier)) {
      return imports[specifier];
    }
    throw new Error(`Unexpected import from ${relativePath}: ${specifier}`);
  };
  const evaluate = new Function('require', 'module', 'exports', output.outputText);
  evaluate(requireFromTest, loadedModule, loadedModule.exports);
  return loadedModule.exports;
}

function loadClient() {
  return loadTypeScriptModule('lib/api/client.ts', {
    'expo/fetch': { fetch: () => Promise.reject(new Error('Unexpected network request.')) },
    '@/lib/api-token': {
      apiTokenStore: {
        clear: async () => undefined,
        read: async () => null,
      },
    },
  });
}

function loadBaseUrlStore(initialValue = null) {
  const values = new Map();
  if (initialValue !== null) {
    values.set('mongars.api-base-url.v1', initialValue);
  }

  const writes = [];
  const secureStore = {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
    deleteItemAsync: async (key) => {
      values.delete(key);
    },
    getItemAsync: async (key) => values.get(key) ?? null,
    isAvailableAsync: async () => true,
    setItemAsync: async (key, value, options) => {
      writes.push({ key, value, options });
      values.set(key, value);
    },
  };
  const client = loadClient();
  const store = loadTypeScriptModule('lib/api-base-url.ts', {
    'expo-secure-store': secureStore,
    '@/lib/api/client': client,
  });

  return { store, values, writes };
}

test.beforeEach(() => {
  delete process.env[configuredUrlKey];
  process.env.EXPO_OS = 'ios';
});

test.afterEach(() => {
  delete process.env[configuredUrlKey];
  delete process.env.EXPO_OS;
});

test('an explicit runtime server URL works without a build-time URL', () => {
  const { normalizeMongarsApiBaseUrl } = loadClient();

  assert.equal(
    normalizeMongarsApiBaseUrl('  https://control.example.test/api///  '),
    'https://control.example.test/api',
  );
});

test('a native runtime URL is saved securely when the build-time URL is absent', async () => {
  const { store, writes } = loadBaseUrlStore();

  const saved = await store.saveApiBaseUrl('https://control.example.test/');

  assert.equal(saved, 'https://control.example.test');
  assert.deepEqual(writes, [
    {
      key: 'mongars.api-base-url.v1',
      value: 'https://control.example.test',
      options: { keychainAccessible: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY' },
    },
  ]);
  assert.equal(await store.readApiBaseUrl(), 'https://control.example.test');
});

test('a persisted native URL hydrates without a build-time URL', async () => {
  const { store } = loadBaseUrlStore('https://saved.example.test');

  assert.equal(await store.readApiBaseUrl(), 'https://saved.example.test');
});

test('an empty native store resolves to an expected missing state', async () => {
  const { store } = loadBaseUrlStore();

  assert.equal(await store.readApiBaseUrl(), null);
});

test('missing runtime and build-time URLs remains a configuration error', () => {
  const { ApiConfigurationError, getMongarsApiBaseUrl } = loadClient();

  assert.throws(
    () => getMongarsApiBaseUrl(),
    (error) =>
      error instanceof ApiConfigurationError &&
      error.message.includes('Open Settings and enter an HTTPS server URL'),
  );
});

test('a native runtime URL cannot persist credentials over LAN HTTP', async () => {
  const { store, writes } = loadBaseUrlStore();

  await assert.rejects(
    () => store.saveApiBaseUrl('http://10.0.0.154:8000'),
    /Use HTTPS before sending a bearer token to a non-loopback server/,
  );
  assert.deepEqual(writes, []);
});
