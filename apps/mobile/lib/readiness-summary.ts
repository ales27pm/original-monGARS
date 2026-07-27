import type { ReadinessResponse } from '@/types/mongars-api';

export type ReadinessTone = 'neutral' | 'positive' | 'warning' | 'danger' | 'primary';

export type ReadinessRowSummary = {
  detail: string | null;
  key: string;
  label: string;
  tone: ReadinessTone;
  value: string;
  blocking: boolean;
};

export function humanizeReadinessValue(value: string | null | undefined): string {
  if (!value) return 'Unknown';
  return value
    .replaceAll('_', ' ')
    .replaceAll('.', ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\w/, (character) => character.toUpperCase());
}

export function readinessBadge(readiness: ReadinessResponse): {
  label: string;
  tone: ReadinessTone;
} {
  return readiness.status === 'ready'
    ? { label: 'Ready', tone: 'positive' }
    : { label: 'Needs attention', tone: 'warning' };
}

export function readinessFailureSummary(readiness: ReadinessResponse): string {
  const blocked = readinessRows(readiness)
    .filter((row) => row.blocking)
    .map((row) => row.label);
  if (blocked.length) return `${blocked.join(', ')} not ready.`;
  if (readiness.status === 'ready') return 'All dependencies are ready.';
  return 'The control plane is reachable, but one or more dependencies are not ready.';
}

export function readinessRows(readiness: ReadinessResponse): ReadinessRowSummary[] {
  const dependencies = readiness.dependencies;
  const rows: ReadinessRowSummary[] = [
    row({
      detail: dependencies.database.error ?? null,
      key: 'database',
      label: 'Database',
      healthy: dependencies.database.healthy,
      value: healthLabel(dependencies.database.healthy),
    }),
    row({
      detail: inferenceDetail(readiness),
      key: 'inference',
      label: 'Cortex',
      healthy: dependencies.inference.healthy,
      value: healthLabel(dependencies.inference.healthy),
    }),
  ];

  if (dependencies.worker) {
    rows.push(
      row({
        detail:
          [
            dependencies.worker.version ? `v${dependencies.worker.version}` : null,
            dependencies.worker.git_sha,
            dependencies.worker.age_seconds !== null
              ? `${Math.round(dependencies.worker.age_seconds)}s old`
              : null,
          ]
            .filter(Boolean)
            .join(' · ') || dependencies.worker.error_code,
        key: 'worker',
        label: 'Worker',
        healthy: dependencies.worker.healthy,
        value: humanizeReadinessValue(dependencies.worker.status),
      }),
    );
  }

  if (dependencies.parser) {
    rows.push(
      row({
        detail: dependencies.parser.version ?? dependencies.parser.error_code,
        key: 'parser',
        label: 'Parser',
        healthy: dependencies.parser.healthy,
        value: healthLabel(dependencies.parser.healthy),
      }),
    );
  }

  if (dependencies.embedding_space) {
    rows.push(
      row({
        detail:
          [
            dependencies.embedding_space.model_alias,
            shortDigest(dependencies.embedding_space.model_digest),
            dependencies.embedding_space.reindex_required
              ? `${dependencies.embedding_space.legacy_chunk_count} legacy chunks`
              : null,
          ]
            .filter(Boolean)
            .join(' · ') || dependencies.embedding_space.error_code,
        key: 'embedding_space',
        label: 'Embedding space',
        healthy: dependencies.embedding_space.healthy,
        value: humanizeReadinessValue(dependencies.embedding_space.status),
      }),
    );
  }

  if (dependencies.evolution_scheduler) {
    rows.push(
      row({
        detail:
          dependencies.evolution_scheduler.reason ??
          (dependencies.evolution_scheduler.can_run ? 'Can run' : 'Cannot run'),
        key: 'evolution_scheduler',
        label: 'Evolution scheduler',
        healthy: dependencies.evolution_scheduler.healthy,
        value: humanizeReadinessValue(dependencies.evolution_scheduler.status),
      }),
    );
  }

  if (dependencies.model_governance) {
    rows.push(
      row({
        detail:
          [
            dependencies.model_governance.candidate_registry.active_alias,
            dependencies.model_governance.candidate_registry.active_generation !== null
              ? `generation ${dependencies.model_governance.candidate_registry.active_generation}`
              : null,
            shortDigest(dependencies.model_governance.candidate_registry.active_digest),
            dependencies.model_governance.candidate_registry.rollback_target_alias
              ? `rollback ${dependencies.model_governance.candidate_registry.rollback_target_alias}`
              : null,
          ]
            .filter(Boolean)
            .join(' · ') || dependencies.model_governance.reason,
        key: 'model_governance',
        label: 'Model governance',
        healthy: dependencies.model_governance.healthy,
        value: humanizeReadinessValue(dependencies.model_governance.status),
      }),
    );
  }

  if (dependencies.executor_security) {
    rows.push(
      row({
        detail: dependencies.executor_security.requires_approval
          ? 'Approval required'
          : dependencies.executor_security.reason,
        key: 'executor_security',
        label: 'Executor security',
        healthy: dependencies.executor_security.healthy,
        value: humanizeReadinessValue(dependencies.executor_security.status),
      }),
    );
  }

  if (dependencies.p2p?.enabled) {
    rows.push(
      row({
        detail: dependencies.p2p.error_code,
        key: 'p2p',
        label: 'P2P',
        healthy: dependencies.p2p.healthy,
        value: healthLabel(dependencies.p2p.healthy),
      }),
    );
  }

  return rows;
}

function row({
  detail,
  healthy,
  key,
  label,
  value,
}: {
  detail: string | null | undefined;
  healthy: boolean | undefined;
  key: string;
  label: string;
  value: string;
}): ReadinessRowSummary {
  return {
    detail: detail ?? null,
    key,
    label,
    tone: toneForHealth(healthy),
    value,
    blocking: healthy === false,
  };
}

function healthLabel(healthy: boolean | undefined): string {
  if (healthy === true) return 'Ready';
  if (healthy === false) return 'Blocked';
  return 'Unknown';
}

function inferenceDetail(readiness: ReadinessResponse): string {
  const inference = readiness.dependencies.inference;
  if (inference.healthy) return `${humanizeReadinessValue(inference.backend)} ready`;
  if (inference.backend_reachable) return 'Required models missing';
  return inference.error_code ?? 'Backend unavailable';
}

function toneForHealth(healthy: boolean | undefined): ReadinessTone {
  if (healthy === true) return 'positive';
  if (healthy === false) return 'danger';
  return 'warning';
}

function shortDigest(digest: string | null | undefined): string | null {
  if (!digest) return null;
  return digest.length > 18 ? `${digest.slice(0, 12)}...${digest.slice(-6)}` : digest;
}
