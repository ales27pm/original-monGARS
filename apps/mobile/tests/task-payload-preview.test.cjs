const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../lib/task-payload-preview.ts');
const source = fs.readFileSync(filename, 'utf8');
const output = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: filename,
  reportDiagnostics: true,
});
assert.equal(output.diagnostics?.length ?? 0, 0);
const loadedModule = { exports: {} };
new Function('require', 'module', 'exports', output.outputText)(
  (specifier) => {
    throw new Error(`Unexpected runtime import: ${specifier}`);
  },
  loadedModule,
  loadedModule.exports,
);
const { formatPayloadBytes, payloadSummaryPreview } = loadedModule.exports;

test('large approval summaries render only the bounded server-provided edges', () => {
  const preview = payloadSummaryPreview({
    preview_head: '{\n  "text": "head',
    preview_tail: 'tail"\n}',
    preview_omitted_characters: 1_999_000,
  });

  assert.match(preview, /^\{\n  "text": "head/);
  assert.match(preview, /1,999,000 characters omitted/);
  assert.match(preview, /tail"\n\}$/);
  assert.match(formatPayloadBytes(2_100_000), /MiB$/);
});

test('small approval summaries remain exact', () => {
  const exact = '{\n  "message": "Bonjour, Laval"\n}';

  assert.equal(
    payloadSummaryPreview({
      preview_head: exact,
      preview_tail: '',
      preview_omitted_characters: 0,
    }),
    exact,
  );
});
