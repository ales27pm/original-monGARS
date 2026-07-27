const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repositoryRoot = path.resolve(__dirname, '../../..');

test('mobile and browser chat clients always send an explicit web-search mode', () => {
  const mobileChat = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/app/(tabs)/(chat)/index.tsx'),
    'utf8',
  );
  const browserScript = fs.readFileSync(
    path.join(repositoryRoot, 'src/mongars/web/static/app.js'),
    'utf8',
  );
  const browserHtml = fs.readFileSync(
    path.join(repositoryRoot, 'src/mongars/web/static/index.html'),
    'utf8',
  );

  assert.match(mobileChat, /web_search: webSearchMode/);
  assert.match(browserScript, /web_search: dom\.webSearchMode\.value/);
  assert.match(browserHtml, /id="web-search-mode"/);
  for (const mode of ['off', 'auto', 'required']) {
    assert.match(browserHtml, new RegExp(`<option value="${mode}"`));
  }
});

test('mobile approval UI fetches only one server-bounded payload page at a time', () => {
  const tasksScreen = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/app/(tabs)/(tasks)/index.tsx'),
    'utf8',
  );

  const client = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/lib/api/client.ts'),
    'utf8',
  );

  assert.doesNotMatch(tasksScreen, /JSON\.stringify\(detail\.data\.payload/);
  assert.doesNotMatch(tasksScreen, /buildPayloadDocument|payloadDocument\.serialized/);
  assert.match(
    tasksScreen,
    /useTaskPayloadPage\(\s*reviewTaskId \?\? '',\s*payloadPageIndex,\s*detail\.data\?\.action_digest/,
  );
  assert.match(tasksScreen, /currentPayloadPage\?\.content/);
  assert.match(tasksScreen, /Open exact payload pages/);
  assert.match(client, /\/payload\?page=\$\{safePage\}/);
  assert.match(client, /body: \{ action_digest: actionDigest \}/);
});

test('mobile settings cannot test a token against an unsaved server URL draft', () => {
  const settingsScreen = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/app/(tabs)/(settings)/index.tsx'),
    'utf8',
  );

  assert.match(
    settingsScreen,
    /isActiveMongarsApiBaseUrlDraft\(serverUrl, baseUrl\)/,
  );
  assert.match(settingsScreen, /draftMatchesActiveBaseUrl &&/);
  assert.match(settingsScreen, /if \(!draftMatchesActiveBaseUrl\)/);
  assert.match(settingsScreen, /Save this server URL before entering or testing its API token/);
});

test('mobile web bootstrap can use the public API URL on the first render', () => {
  const provider = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/providers/mongars-provider.tsx'),
    'utf8',
  );
  const baseUrlStore = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/lib/api-base-url.ts'),
    'utf8',
  );

  assert.match(provider, /canUseBuildTimeApiBaseUrlImmediately\(\) \? 'missing' : 'loading'/);
  assert.match(baseUrlStore, /process\.env\.EXPO_OS === 'web'/);
  assert.match(baseUrlStore, /process\.env\.EXPO_OS === 'ios'/);
  assert.match(baseUrlStore, /process\.env\.EXPO_OS === 'android'/);
});

test('Expo Router owns navigation imports for SDK 57 static rendering', () => {
  const rootLayout = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/app/_layout.tsx'),
    'utf8',
  );
  const hapticTab = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/components/haptic-tab.tsx'),
    'utf8',
  );

  assert.doesNotMatch(rootLayout, /from ['"]@react-navigation\//);
  assert.doesNotMatch(hapticTab, /from ['"]@react-navigation\//);
  assert.match(rootLayout, /from 'expo-router'/);
  assert.match(hapticTab, /from 'expo-router\/react-navigation'/);
});

test('frontends surface backend evolution readiness dependencies', () => {
  const mobileTypes = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/types/mongars-api.ts'),
    'utf8',
  );
  const mobileSettings = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/app/(tabs)/(settings)/index.tsx'),
    'utf8',
  );
  const mobileReadinessSummary = fs.readFileSync(
    path.join(repositoryRoot, 'apps/mobile/lib/readiness-summary.ts'),
    'utf8',
  );
  const browserScript = fs.readFileSync(
    path.join(repositoryRoot, 'src/mongars/web/static/app.js'),
    'utf8',
  );
  const browserHtml = fs.readFileSync(
    path.join(repositoryRoot, 'src/mongars/web/static/index.html'),
    'utf8',
  );

  for (const key of ['evolution_scheduler', 'model_governance', 'executor_security']) {
    assert.match(mobileTypes, new RegExp(`${key}\\?:`));
    assert.match(browserScript, new RegExp(`dependencies\\.${key}`));
  }
  for (const label of ['Evolution scheduler', 'Model governance', 'Executor security']) {
    assert.match(mobileReadinessSummary, new RegExp(label));
  }
  assert.match(mobileSettings, /readinessRows\(readiness\)/);
  for (const id of ['evolution-status', 'governance-status', 'executor-status']) {
    assert.match(browserHtml, new RegExp(`id="${id}"`));
    assert.match(browserScript, new RegExp(`#${id}`));
  }
});
