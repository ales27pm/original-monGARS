const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');

function loadTypeScriptModule(relativePath, imports = {}) {
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

function readinessFixture(overrides = {}) {
  return {
    status: 'not_ready',
    dependencies: {
      database: { healthy: true },
      inference: {
        backend: 'ollama',
        healthy: true,
        backend_reachable: true,
        chat_model_ready: true,
        embedding_model_ready: true,
        latency_ms: 1.2,
        error_code: null,
      },
      worker: {
        healthy: true,
        status: 'healthy',
        component_id: 'worker:primary',
        instance_id: '11111111-2222-3333-4444-555555555555',
        version: '0.1.0',
        git_sha: 'abc123',
        last_seen_at: '2026-07-26T12:00:00+00:00',
        age_seconds: 2,
        error_code: null,
      },
      parser: { healthy: true, version: 'parser-v1', error_code: null },
      embedding_space: {
        healthy: true,
        status: 'ready',
        space_id: 'space-1',
        model_alias: 'nomic-embed-text',
        model_digest: 'a'.repeat(64),
        dimension: 768,
        worker_space_id: 'space-1',
        total_chunk_count: 10,
        compatible_chunk_count: 10,
        legacy_chunk_count: 0,
        reindex_required: false,
        error_code: null,
      },
      evolution_scheduler: {
        enabled: true,
        status: 'ready',
        healthy: true,
        reason: null,
        can_run: true,
        budgets: {},
      },
      model_governance: {
        enabled: true,
        status: 'blocked',
        healthy: false,
        reason: 'active_model_not_fully_configured',
        candidate_registry: {
          active_alias: 'qwen3:4b-instruct',
          active_digest: null,
          active_generation: 1,
          prior_generation_anchor: null,
          rollback_target_alias: 'rollback-candidate',
          rollback_target_digest: 'b'.repeat(64),
        },
        benchmarks: {
          scoring_policy_version: 'bench-v2',
          benchmarking_policy_version: 'suite-v1',
          minimum_sample_size: 500,
          promotion_quality_threshold: 0.91,
          rollback_quality_threshold: 0.81,
        },
      },
      executor_security: {
        enabled: false,
        status: 'disabled_by_default',
        healthy: true,
        reason: 'executor security review not yet approved',
        approved_kinds: ['evolution.proposal.generate'],
        requires_approval: true,
      },
    },
    ...overrides,
  };
}

test('readiness summary includes backend evolution and governance rows', () => {
  const summary = loadTypeScriptModule('lib/readiness-summary.ts');
  const rows = summary.readinessRows(readinessFixture());

  assert.deepEqual(
    rows.map((row) => row.key),
    [
      'database',
      'inference',
      'worker',
      'parser',
      'embedding_space',
      'evolution_scheduler',
      'model_governance',
      'executor_security',
    ],
  );
  const governance = rows.find((row) => row.key === 'model_governance');
  assert.equal(governance.blocking, true);
  assert.equal(governance.tone, 'danger');
  assert.equal(governance.value, 'Blocked');
  assert.match(governance.detail, /qwen3:4b-instruct/);
  assert.equal(summary.readinessFailureSummary(readinessFixture()), 'Model governance not ready.');
});

test('readiness summary reports ready snapshots without false blockers', () => {
  const summary = loadTypeScriptModule('lib/readiness-summary.ts');
  const ready = readinessFixture({
    status: 'ready',
    dependencies: {
      ...readinessFixture().dependencies,
      model_governance: {
        ...readinessFixture().dependencies.model_governance,
        status: 'ready',
        healthy: true,
        reason: null,
        candidate_registry: {
          ...readinessFixture().dependencies.model_governance.candidate_registry,
          active_digest: 'c'.repeat(64),
        },
      },
    },
  });

  assert.deepEqual(summary.readinessBadge(ready), { label: 'Ready', tone: 'positive' });
  assert.equal(summary.readinessFailureSummary(ready), 'All dependencies are ready.');
  assert.equal(summary.readinessRows(ready).some((row) => row.blocking), false);
});
