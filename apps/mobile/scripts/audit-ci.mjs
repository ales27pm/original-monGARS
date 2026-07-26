#!/usr/bin/env node

import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import { evaluateAuditReport } from './audit-policy.mjs';

const mobileRoot = fileURLToPath(new URL('../', import.meta.url));
const policyPath = fileURLToPath(
  new URL('../security/npm-audit-exceptions.json', import.meta.url),
);
const policy = JSON.parse(readFileSync(policyPath, 'utf8'));

const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const audit = spawnSync(npmCommand, ['audit', '--json'], {
  cwd: mobileRoot,
  encoding: 'utf8',
  maxBuffer: 32 * 1024 * 1024,
  shell: false,
});

if (audit.error) {
  console.error(`npm audit failed to start: ${audit.error.message}`);
  process.exit(2);
}
if (audit.status !== 0 && audit.status !== 1) {
  console.error(audit.stderr || `npm audit exited with status ${audit.status}`);
  process.exit(2);
}

let report;
try {
  report = JSON.parse(audit.stdout);
} catch (error) {
  console.error('npm audit returned invalid JSON');
  console.error(error);
  process.exit(2);
}

let evaluation;
try {
  evaluation = evaluateAuditReport(report, policy);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(2);
}

if (evaluation.rejected.length > 0) {
  console.error('npm audit policy rejected high/critical vulnerabilities:');
  for (const rejection of evaluation.rejected) {
    console.error(`- ${rejection.name}`);
    for (const reason of rejection.reasons) {
      console.error(`  - ${reason}`);
    }
  }
  process.exit(1);
}

for (const exception of evaluation.allowed) {
  const roots = exception.roots
    .map((root) => `${root.advisory} (${root.package})`)
    .join(', ');
  console.warn(`temporary npm audit exception: ${exception.name} -> ${roots}`);
}

const counts = report.metadata?.vulnerabilities ?? {};
console.log(
  `npm audit policy passed; unexpected high/critical: 0; ` +
    `reported low=${counts.low ?? 0}, moderate=${counts.moderate ?? 0}, ` +
    `high=${counts.high ?? 0}, critical=${counts.critical ?? 0}`,
);
