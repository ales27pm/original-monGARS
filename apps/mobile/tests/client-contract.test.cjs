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
