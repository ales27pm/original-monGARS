const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const mobileRoot = path.resolve(__dirname, '..');

function read(relativePath) {
  return fs.readFileSync(path.join(mobileRoot, relativePath), 'utf8');
}

test('document upload uses the SDK-compatible picker and streaming file APIs', () => {
  const packageJson = JSON.parse(read('package.json'));
  const memoryScreen = read('app/(tabs)/(memory)/index.tsx');
  const preparation = read('lib/document-upload.ts');

  assert.match(packageJson.dependencies['expo-document-picker'], /^~14\./);
  assert.match(packageJson.dependencies['expo-file-system'], /^~19\./);
  assert.match(memoryScreen, /DocumentPicker\.getDocumentAsync\(\{/);
  assert.match(memoryScreen, /copyToCacheDirectory: true/);
  assert.match(memoryScreen, /multiple: false/);
  assert.match(memoryScreen, /base64: false/);
  assert.match(preparation, /new File\(asset\.uri\)/);
  assert.match(preparation, /sourceFile\.slice\(0, measuredSize, expectedType\)/);
  assert.match(preparation, /\\p\{Cf\}/u);
  assert.doesNotMatch(preparation, /\.base64(?:Sync)?\(/);
  assert.doesNotMatch(memoryScreen, /\.base64(?:Sync)?\(/);
});

test('document upload sends the complete multipart governance envelope', () => {
  const client = read('lib/api/client.ts');
  const uploadMethod = client.slice(
    client.indexOf('  uploadDocument('),
    client.indexOf('\n  listTasks(', client.indexOf('  uploadDocument(')),
  );

  assert.match(uploadMethod, /new FormData\(\)/);
  assert.match(uploadMethod, /append\('file', request\.file, request\.filename\)/);
  assert.match(uploadMethod, /append\('declared_size', String\(request\.declared_size\)\)/);
  assert.match(uploadMethod, /append\('source_timestamp', sourceTimestamp\.toISOString\(\)\)/);
  assert.match(uploadMethod, /append\('sensitivity', request\.sensitivity\)/);
  assert.match(uploadMethod, /append\('retention_class', request\.retention_class\)/);
  assert.match(uploadMethod, /append\('title', request\.title\.trim\(\)\)/);
  assert.match(uploadMethod, /this\.request\('\/v1\/documents'/);
  assert.match(uploadMethod, /multipartBody/);
  assert.doesNotMatch(uploadMethod, /headers\.set\('Content-Type'/);
  assert.doesNotMatch(uploadMethod, /JSON\.stringify/);
});

test('memory UI exposes approval, governance, loading, cancellation, and error states', () => {
  const memoryScreen = read('app/(tabs)/(memory)/index.tsx');

  assert.match(memoryScreen, /title="Import a document"/);
  assert.match(memoryScreen, /sensitivityOptions\.map/);
  assert.match(memoryScreen, /retentionOptions\.map/);
  assert.match(memoryScreen, /declared_size: selectedDocument\.size/);
  assert.match(memoryScreen, /source_timestamp: selectedDocument\.sourceTimestamp/);
  assert.match(memoryScreen, /title: title\.trim\(\) \|\| null/);
  assert.match(memoryScreen, /Uploading \{selectedDocument\.filename\} securely/);
  assert.match(memoryScreen, /onPress=\{upload\.cancel\}/);
  assert.match(memoryScreen, /upload\.error \?\? selectionError/);
  assert.match(memoryScreen, /Approval required/);
  assert.match(memoryScreen, /upload\.data\.action_digest/);
});
