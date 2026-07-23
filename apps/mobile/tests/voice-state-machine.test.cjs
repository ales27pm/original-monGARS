const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../lib/voice-state-machine.ts');
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

const sandbox = { exports: {} };
new Function('require', 'module', 'exports', output.outputText)(
  () => {
    throw new Error('Runtime import is not expected in voice state machine tests.');
  },
  sandbox,
  sandbox.exports,
);

const { nextVoiceState, canTransition } = sandbox.exports;

const chain = [
  ['idle', 'start_push_to_talk', 'requesting_permission'],
  ['requesting_permission', 'permission_granted', 'listening'],
  ['listening', 'stop_recording', 'finalizing'],
  ['finalizing', 'transcription_complete', 'thinking'],
  ['thinking', 'speak_complete', 'speaking'],
  ['speaking', 'speak_complete', 'idle'],
];

for (const [start, event, expected] of chain) {
  test(`${start} -> ${expected} on ${event}`, () => {
    assert.equal(nextVoiceState(start, event), expected);
  });
}

test('idle transitions are deterministic', () => {
  assert.equal(nextVoiceState('idle', 'start_push_to_talk'), 'requesting_permission');
  assert.throws(() => nextVoiceState('idle', 'stop_recording'));
});

test('listening cannot jump directly to speaking', () => {
  assert.equal(canTransition('listening', 'speak_complete'), false);
  assert.throws(() => nextVoiceState('listening', 'speak_complete'));
});

test('requesting permission can only progress to listening or idle', () => {
  assert.equal(nextVoiceState('requesting_permission', 'permission_denied'), 'idle');
  assert.equal(canTransition('requesting_permission', 'permission_denied'), true);
  assert.equal(canTransition('requesting_permission', 'transcription_complete'), false);
});

test('silence timeout from listening moves to finalizing', () => {
  assert.equal(nextVoiceState('listening', 'silence_timeout'), 'finalizing');
});

test('continuous mode auto-restart transitions speaking to listening', () => {
  assert.equal(nextVoiceState('speaking', 'auto_restart'), 'listening');
});

test('auto_restart is not valid from non-speaking states', () => {
  assert.throws(() => nextVoiceState('finalizing', 'auto_restart'));
  assert.throws(() => nextVoiceState('requesting_permission', 'auto_restart'));
});

test('network loss sends listening and finalizing states back to idle', () => {
  assert.equal(nextVoiceState('listening', 'network_lost'), 'idle');
  assert.equal(nextVoiceState('finalizing', 'network_lost'), 'idle');
  assert.equal(nextVoiceState('speaking', 'network_lost'), 'idle');
});

test('tts stop is explicit in speaking only', () => {
  assert.equal(nextVoiceState('speaking', 'tts_stopped'), 'idle');
  assert.equal(canTransition('finalizing', 'tts_stopped'), false);
});

test('interruption transitions safely to idle from active voice states', () => {
  assert.equal(nextVoiceState('listening', 'interruption'), 'idle');
  assert.equal(nextVoiceState('finalizing', 'interruption'), 'idle');
  assert.equal(nextVoiceState('thinking', 'interruption'), 'idle');
  assert.equal(nextVoiceState('speaking', 'interruption'), 'idle');
});

test('permission denial and cancellation are deterministic safe exits', () => {
  assert.equal(nextVoiceState('requesting_permission', 'permission_denied'), 'idle');
  assert.equal(nextVoiceState('listening', 'cancel'), 'idle');
  assert.equal(nextVoiceState('speaking', 'cancel'), 'idle');
  assert.equal(canTransition('requesting_permission', 'transcription_complete'), false);
});
