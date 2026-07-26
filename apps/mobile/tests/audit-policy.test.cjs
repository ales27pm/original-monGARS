const assert = require('node:assert/strict');
const test = require('node:test');

const policyModule = import('../scripts/audit-policy.mjs');

function allowedPolicy(expiresOn = '2099-12-31') {
  return {
    schema_version: 1,
    exceptions: [
      {
        advisory: 'GHSA-mh99-v99m-4gvg',
        package: 'brace-expansion',
        max_severity: 'high',
        require_transitive: true,
        expires_on: expiresOn,
        reason: 'test policy',
      },
    ],
  };
}

function allowedReport() {
  return {
    metadata: {
      vulnerabilities: {
        info: 0,
        low: 0,
        moderate: 0,
        high: 3,
        critical: 0,
      },
    },
    vulnerabilities: {
      'brace-expansion': {
        name: 'brace-expansion',
        severity: 'high',
        isDirect: false,
        via: [
          {
            dependency: 'brace-expansion',
            severity: 'high',
            url: 'https://github.com/advisories/GHSA-mh99-v99m-4gvg',
          },
        ],
      },
      minimatch: {
        name: 'minimatch',
        severity: 'high',
        isDirect: false,
        via: ['brace-expansion'],
      },
      expo: {
        name: 'expo',
        severity: 'high',
        isDirect: true,
        via: ['minimatch'],
      },
    },
  };
}

test('audit policy permits only the exact unexpired transitive advisory', async () => {
  const { evaluateAuditReport } = await policyModule;
  const result = evaluateAuditReport(
    allowedReport(),
    allowedPolicy(),
    '2026-07-25',
  );

  assert.equal(result.rejected.length, 0);
  assert.deepEqual(
    result.allowed.map((entry) => entry.name).sort(),
    ['brace-expansion', 'expo', 'minimatch'],
  );
});

test('audit policy rejects an unexpected high advisory', async () => {
  const { evaluateAuditReport } = await policyModule;
  const report = allowedReport();
  report.vulnerabilities.postcss = {
    name: 'postcss',
    severity: 'high',
    isDirect: false,
    via: [
      {
        dependency: 'postcss',
        severity: 'high',
        url: 'https://github.com/advisories/GHSA-r28c-9q8g-f849',
      },
    ],
  };

  const result = evaluateAuditReport(
    report,
    allowedPolicy(),
    '2026-07-25',
  );

  assert.equal(result.rejected.length, 1);
  assert.equal(result.rejected[0].name, 'postcss');
});

test('audit policy fails closed when an exception expires', async () => {
  const { evaluateAuditReport } = await policyModule;

  assert.throws(
    () =>
      evaluateAuditReport(
        allowedReport(),
        allowedPolicy('2026-07-24'),
        '2026-07-25',
      ),
    /expired/,
  );
});

test('audit policy ignores moderate roots inside a high aggregate dependency chain', async () => {
  const { evaluateAuditReport } = await policyModule;
  const report = allowedReport();

  report.vulnerabilities.uuid = {
    name: 'uuid',
    severity: 'moderate',
    isDirect: false,
    via: [
      {
        dependency: 'uuid',
        severity: 'moderate',
        url: 'https://github.com/advisories/GHSA-w5hq-g745-h8pq',
      },
    ],
  };
  report.vulnerabilities.expo.via.push('uuid');

  const result = evaluateAuditReport(
    report,
    allowedPolicy(),
    '2026-07-25',
  );

  assert.equal(result.rejected.length, 0);

  const expo = result.allowed.find((entry) => entry.name === 'expo');
  assert.ok(expo);
  assert.deepEqual(
    expo.roots.map((root) => root.advisory),
    ['GHSA-MH99-V99M-4GVG'],
  );
});

