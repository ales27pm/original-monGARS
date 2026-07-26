const GHSA_PATTERN =
  /GHSA-[23456789CFGHJMPQRVWX]{4}-[23456789CFGHJMPQRVWX]{4}-[23456789CFGHJMPQRVWX]{4}/i;

const SEVERITY_RANK = new Map([
  ['info', 0],
  ['low', 1],
  ['moderate', 2],
  ['high', 3],
  ['critical', 4],
]);

function severityRank(value) {
  return SEVERITY_RANK.get(String(value).toLowerCase()) ?? Number.POSITIVE_INFINITY;
}

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function resolveRootAdvisories(name, vulnerabilities, trail = new Set()) {
  if (trail.has(name)) {
    return {
      roots: [],
      unresolved: [`dependency cycle while resolving ${name}`],
    };
  }

  const entry = vulnerabilities[name];
  if (!isRecord(entry) || !Array.isArray(entry.via)) {
    return {
      roots: [],
      unresolved: [`missing audit entry for ${name}`],
    };
  }

  const nextTrail = new Set(trail);
  nextTrail.add(name);

  const roots = [];
  const unresolved = [];

  for (const via of entry.via) {
    if (typeof via === 'string') {
      const nested = resolveRootAdvisories(via, vulnerabilities, nextTrail);
      roots.push(...nested.roots);
      unresolved.push(...nested.unresolved);
      continue;
    }

    if (!isRecord(via)) {
      unresolved.push(`${name} contains an invalid audit cause`);
      continue;
    }

    const url = typeof via.url === 'string' ? via.url : '';
    const advisoryMatch = url.match(GHSA_PATTERN);
    if (!advisoryMatch) {
      unresolved.push(`${name} contains an advisory without a GHSA identifier`);
      continue;
    }

    const packageName =
      typeof via.dependency === 'string'
        ? via.dependency
        : typeof via.name === 'string'
          ? via.name
          : name;

    roots.push({
      advisory: advisoryMatch[0].toUpperCase(),
      package: packageName,
      severity: typeof via.severity === 'string' ? via.severity : entry.severity,
    });
  }

  if (roots.length === 0 && unresolved.length === 0) {
    unresolved.push(`${name} has no resolvable root advisory`);
  }

  return { roots, unresolved };
}

function normalizedExceptions(policy, today) {
  if (!isRecord(policy) || policy.schema_version !== 1 || !Array.isArray(policy.exceptions)) {
    throw new TypeError('npm audit policy has an invalid schema');
  }

  const exceptions = new Map();
  for (const rule of policy.exceptions) {
    if (
      !isRecord(rule) ||
      typeof rule.advisory !== 'string' ||
      typeof rule.package !== 'string' ||
      typeof rule.expires_on !== 'string' ||
      typeof rule.max_severity !== 'string'
    ) {
      throw new TypeError('npm audit policy contains an invalid exception');
    }

    const advisory = rule.advisory.toUpperCase();
    if (!GHSA_PATTERN.test(advisory)) {
      throw new TypeError(`npm audit policy contains an invalid advisory: ${advisory}`);
    }
    if (exceptions.has(advisory)) {
      throw new TypeError(`npm audit policy repeats advisory ${advisory}`);
    }
    if (rule.expires_on < today) {
      throw new Error(
        `npm audit exception ${advisory} expired on ${rule.expires_on}`,
      );
    }

    exceptions.set(advisory, {
      advisory,
      package: rule.package,
      expires_on: rule.expires_on,
      max_severity: rule.max_severity,
      require_transitive: rule.require_transitive === true,
      reason: typeof rule.reason === 'string' ? rule.reason : '',
    });
  }
  return exceptions;
}

export function evaluateAuditReport(
  report,
  policy,
  today = new Date().toISOString().slice(0, 10),
) {
  if (!isRecord(report)) {
    throw new TypeError('npm audit did not return a JSON object');
  }
  if (isRecord(report.error)) {
    throw new Error(
      typeof report.error.summary === 'string'
        ? report.error.summary
        : 'npm audit returned an error',
    );
  }

  const vulnerabilities = isRecord(report.vulnerabilities)
    ? report.vulnerabilities
    : {};
  const exceptions = normalizedExceptions(policy, today);
  const allowed = [];
  const rejected = [];

  for (const [name, rawEntry] of Object.entries(vulnerabilities)) {
    if (!isRecord(rawEntry) || severityRank(rawEntry.severity) < severityRank('high')) {
      continue;
    }

    const { roots, unresolved } = resolveRootAdvisories(name, vulnerabilities);
    const uniqueRoots = [
      ...new Map(
        roots.map((root) => [
          `${root.advisory}:${root.package}`,
          root,
        ]),
      ).values(),
    ];
    const highOrCriticalRoots = uniqueRoots.filter(
      (root) => severityRank(root.severity) >= severityRank('high'),
    );
    const reasons = [...unresolved];

    for (const root of highOrCriticalRoots) {
      const rule = exceptions.get(root.advisory);
      if (!rule) {
        reasons.push(
          `${root.advisory} affecting ${root.package} is not allowlisted`,
        );
        continue;
      }
      if (root.package !== rule.package) {
        reasons.push(
          `${root.advisory} resolved to ${root.package}, expected ${rule.package}`,
        );
      }
      if (severityRank(root.severity) > severityRank(rule.max_severity)) {
        reasons.push(
          `${root.advisory} severity ${root.severity} exceeds ${rule.max_severity}`,
        );
      }
      if (rule.require_transitive) {
        const rootEntry = vulnerabilities[root.package];
        if (!isRecord(rootEntry) || rootEntry.isDirect !== false) {
          reasons.push(
            `${root.advisory} is no longer strictly transitive`,
          );
        }
      }
    }

    if (highOrCriticalRoots.length === 0) {
      reasons.push(`${name} has no high/critical root advisory`);
    }

    if (reasons.length > 0) {
      rejected.push({ name, reasons, roots: highOrCriticalRoots });
    } else {
      allowed.push({ name, roots: highOrCriticalRoots });
    }
  }

  return {
    allowed,
    rejected,
    metadata: isRecord(report.metadata) ? report.metadata : {},
  };
}
