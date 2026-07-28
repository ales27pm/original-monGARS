const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const mobileRoot = path.resolve(__dirname, '..');

function read(relativePath) {
  return fs.readFileSync(path.join(mobileRoot, relativePath), 'utf8');
}

test('chat keeps the composer docked outside the scrolling conversation', () => {
  const screen = read('app/(tabs)/(chat)/index.tsx');
  const scrollEnd = screen.lastIndexOf('</ScrollView>');
  const composer = screen.lastIndexOf('<ChatComposer');

  assert.match(screen, /<KeyboardAvoidingView/);
  assert.ok(scrollEnd > 0, 'chat must have a dedicated conversation scroll view');
  assert.ok(composer > scrollEnd, 'composer must remain outside the scrolling conversation');
});

test('chat does not expose the unfinished voice diagnostics dashboard', () => {
  const screen = read('app/(tabs)/(chat)/index.tsx');
  const retiredPrototypeCopy = [
    'VOICE LOOP',
    'SAFE FOUNDATION',
    'Waveform fallback',
    'STT identity',
    'Request limits',
    'Request permission',
    'Continuous loop',
  ];

  for (const copy of retiredPrototypeCopy) {
    assert.equal(screen.includes(copy), false, `retired prototype copy returned: ${copy}`);
  }
});

test('conversation-first UI preserves chat safety and interaction contracts', () => {
  const screen = read('app/(tabs)/(chat)/index.tsx');
  const composer = read('components/chat-composer.tsx');
  const tabs = read('components/tab-glyph.tsx');

  assert.match(screen, /require_local_only:\s*true/);
  assert.match(screen, /web_search:\s*webSearchMode/);
  assert.match(screen, /<ChatFeedbackControls\s+traceId=/);
  assert.match(screen, /<ChatEmptyState/);

  assert.match(composer, /accessibilityLabel="Message Cortex"/);
  assert.match(composer, /accessibilityLabel="Send message"/);
  assert.match(composer, /accessibilityLabel="Cancel response"/);
  assert.match(composer, /accessibilityRole="radiogroup"/);
  assert.match(composer, /height:\s*44/);
  assert.match(composer, /width:\s*44/);

  assert.match(tabs, /<SystemIcon/);
  assert.doesNotMatch(tabs, /chat:\s*'●'|memory:\s*'◆'|settings:\s*'⚙'/);
});
