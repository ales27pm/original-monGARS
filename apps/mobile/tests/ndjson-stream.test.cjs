'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function loadDecoderModule() {
  const filename = path.join(__dirname, '..', 'lib', 'api', 'ndjson.ts');
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      strict: true,
    },
    fileName: filename,
    reportDiagnostics: true,
  });
  const errors = (output.diagnostics ?? []).filter(
    (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
  );
  assert.deepEqual(errors, []);
  const module = { exports: {} };
  const execute = new Function('exports', 'require', 'module', '__filename', '__dirname', output.outputText);
  execute(module.exports, require, module, filename, path.dirname(filename));
  return module.exports;
}

const { ChatNdjsonDecoder, ChatStreamProtocolError, parseChatStreamFrameLine } =
  loadDecoderModule();


test('decodes split UTF-8 and a final line without a newline', () => {
  const decoder = new ChatNdjsonDecoder();
  const payload = [
    JSON.stringify({ type: 'start', trace_id: 'trc_test', session_id: 'session-test' }),
    JSON.stringify({ type: 'sources', sources: [] }),
    JSON.stringify({ type: 'delta', text: 'Québec 🚀' }),
    JSON.stringify({
      type: 'final',
      trace_id: 'trc_test',
      session_id: 'session-test',
      status: 'ok',
      answer: 'Québec 🚀',
      model: 'qwen3:4b',
      memory_hits: 0,
      web_search_status: 'not_requested',
      sources: [],
      citations: [],
    }),
  ].join('\n');
  const bytes = new TextEncoder().encode(payload);
  const rocket = new TextEncoder().encode('🚀');
  const rocketIndex = bytes.findIndex((value, index) =>
    rocket.every((byte, offset) => bytes[index + offset] === byte),
  );
  assert.ok(rocketIndex > 0);

  const frames = [
    ...decoder.push(bytes.slice(0, rocketIndex + 1)),
    ...decoder.push(bytes.slice(rocketIndex + 1, rocketIndex + 3)),
    ...decoder.push(bytes.slice(rocketIndex + 3)),
    ...decoder.finish(),
  ];

  assert.deepEqual(frames.map((frame) => frame.type), ['start', 'sources', 'delta', 'final']);
  assert.equal(frames[2].text, 'Québec 🚀');
  assert.equal(frames[3].answer, 'Québec 🚀');
});


test('rejects a source key whose prefix does not match its kind', () => {
  assert.throws(
    () =>
      parseChatStreamFrameLine(
        JSON.stringify({
          type: 'sources',
          sources: [
            {
              key: 'W1',
              kind: 'memory',
              source_id: null,
              title: null,
              url: null,
              locator: null,
              included: true,
            },
          ],
        }),
      ),
    ChatStreamProtocolError,
  );
});


test('rejects malformed JSON and oversized unfinished lines', () => {
  assert.throws(() => parseChatStreamFrameLine('{bad-json'), ChatStreamProtocolError);
  const decoder = new ChatNdjsonDecoder();
  assert.throws(
    () => decoder.push(new TextEncoder().encode('x'.repeat(1_100_001))),
    ChatStreamProtocolError,
  );
});


test('rejects an oversized individual network chunk', () => {
  const decoder = new ChatNdjsonDecoder();
  assert.throws(
    () => decoder.push(new Uint8Array(2_000_001)),
    ChatStreamProtocolError,
  );
});


test('rejects non-finite locator values and invalid error codes', () => {
  assert.throws(
    () =>
      parseChatStreamFrameLine(
        JSON.stringify({
          type: 'sources',
          sources: [
            {
              key: 'M1',
              kind: 'memory',
              source_id: null,
              title: null,
              url: null,
              locator: { score: 'not-a-number', nested: { value: null } },
              included: 'yes',
            },
          ],
        }),
      ),
    ChatStreamProtocolError,
  );
  assert.throws(
    () => parseChatStreamFrameLine(JSON.stringify({ type: 'error', code: 'BAD CODE', retryable: false })),
    ChatStreamProtocolError,
  );
});
