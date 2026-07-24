const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

function loadDecoder() {
  const sourcePath = path.join(__dirname, '..', 'lib', 'api', 'chat-stream.ts');
  const source = fs.readFileSync(sourcePath, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(output, {
    exports: module.exports,
    module,
    require,
  });
  return module.exports.NdjsonChatDecoder;
}

function finalResponse() {
  return {
    trace_id: 'trc_test',
    session_id: 'session-test',
    status: 'ok',
    answer: 'Hello',
    model: 'local-model',
    memory_hits: 0,
    web_search_status: 'not_requested',
    sources: [],
    citations: [],
  };
}

test('decodes NDJSON across arbitrary transport boundaries', () => {
  const Decoder = loadDecoder();
  const serialized = [
    {
      type: 'start',
      protocol: 'mongars-chat-ndjson-v1',
      stream_id: `str_${'a'.repeat(32)}`,
    },
    { type: 'attempt', attempt: 1 },
    { type: 'delta', attempt: 1, text: 'Hel' },
    { type: 'delta', attempt: 1, text: 'lo' },
    { type: 'final', response: finalResponse() },
  ]
    .map((frame) => `${JSON.stringify(frame)}\n`)
    .join('');

  const decoder = new Decoder();
  const frames = [];
  for (const chunk of [serialized.slice(0, 17), serialized.slice(17, 71), serialized.slice(71)]) {
    frames.push(...decoder.push(chunk));
  }
  frames.push(...decoder.finish());

  assert.deepEqual(
    frames.map((frame) => frame.type),
    ['start', 'attempt', 'delta', 'delta', 'final'],
  );
  assert.equal(frames[2].text, 'Hel');
  assert.equal(frames[4].response.answer, 'Hello');
});

test('accepts trusted citation metadata in the final frame', () => {
  const Decoder = loadDecoder();
  const response = finalResponse();
  response.citations = [
    {
      key: 'M1',
      kind: 'memory',
      source_id: 'chunk-id',
      title: 'Project plan',
      url: null,
      locator: { page_number: 7 },
    },
  ];
  const frames = new Decoder().push(`${JSON.stringify({ type: 'final', response })}\n`);

  assert.equal(frames[0].response.citations[0].key, 'M1');
});

test('enforces the frame limit in UTF-8 bytes without a runtime polyfill', () => {
  const Decoder = loadDecoder();
  const oversized = JSON.stringify({
    type: 'delta',
    attempt: 1,
    text: 'é'.repeat(500_001),
  });

  assert.throws(
    () => new Decoder().push(`${oversized}\n`),
    /exceeded its byte limit/,
  );
});

test('rejects malformed and unsupported stream frames', () => {
  const Decoder = loadDecoder();
  const decoder = new Decoder();

  assert.throws(
    () => decoder.push('{"type":"delta","attempt":0,"text":"bad"}\n'),
    /invalid delta frame/,
  );
  assert.throws(
    () => new Decoder().push('{"type":"made_up"}\n'),
    /unsupported frame/,
  );
  assert.throws(() => new Decoder().push('not-json\n'), /invalid NDJSON/);

  const response = finalResponse();
  response.citations = [
    {
      key: 'M1',
      kind: 'made_up',
      source_id: null,
      title: null,
      url: null,
      locator: null,
    },
  ];
  assert.throws(
    () => new Decoder().push(`${JSON.stringify({ type: 'final', response })}\n`),
    /invalid final frame/,
  );
});
