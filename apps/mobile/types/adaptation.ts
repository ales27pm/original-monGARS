import type { TaskResponse } from '@/types/mongars-api';

export type PersonalityDimension =
  | 'brevity'
  | 'directness'
  | 'formality'
  | 'humor'
  | 'initiative'
  | 'technical_depth';

export type PersonalitySource = 'approved_profile' | 'default' | 'explicit_feedback';

export type PersonalityPreference = {
  dimension: PersonalityDimension;
  value: number;
  confidence: number;
  evidence_count: number;
};

export type PersonalitySnapshot = {
  revision: number;
  source: PersonalitySource;
  profile_digest: string | null;
  schema_version: 'personality-v1';
  preferences: PersonalityPreference[];
};

export type PersonalityRevision = {
  feedback_id: string;
  feedback_digest: string;
  proposal_digest: string;
  task_id: string;
  changed_dimension: PersonalityDimension;
  conflict: boolean;
  created_at: string;
  snapshot: PersonalitySnapshot;
};

export type PersonalityHistoryResponse = {
  items: PersonalityRevision[];
};

export type PersonalityExportResponse = {
  exported_at: string;
  current: PersonalitySnapshot;
  history: PersonalityRevision[];
};

export type ProfileDeltaProposal = {
  changed_dimension: PersonalityDimension;
  conflict: boolean;
  expected_profile_digest: string;
  expected_revision: number;
  feedback_digest: string;
  feedback_id: string;
  previous: PersonalityPreference | null;
  proposed: PersonalityPreference;
  target_preferences: PersonalityPreference[];
  target_profile_digest: string;
  target_revision: number;
};

export type CorrectionFeedbackRequest = {
  kind: 'correction';
  feedback_id: string;
  response_trace_id: string;
  correction_text: string;
};

export type HelpfulnessFeedbackRequest = {
  kind: 'helpfulness';
  feedback_id: string;
  response_trace_id: string;
  helpful: boolean;
};

export type PreferenceFeedbackRequest = {
  kind: 'preference';
  feedback_id: string;
  response_trace_id?: string | null;
  dimension: PersonalityDimension;
  desired_value: number;
};

export type ExplicitFeedbackRequest =
  | CorrectionFeedbackRequest
  | HelpfulnessFeedbackRequest
  | PreferenceFeedbackRequest;

export type ExplicitFeedbackResponse = {
  feedback_id: string;
  kind: ExplicitFeedbackRequest['kind'];
  feedback_digest: string;
  created: boolean;
  applied_task_id: string | null;
  applied_revision: number | null;
  proposal: ProfileDeltaProposal | null;
  profile: PersonalitySnapshot;
  proposal_task: TaskResponse | null;
};

export function isPersonalitySnapshot(value: unknown): value is PersonalitySnapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.revision === 'number' &&
    Number.isSafeInteger(candidate.revision) &&
    candidate.revision >= 0 &&
    typeof candidate.source === 'string' &&
    ['approved_profile', 'default', 'explicit_feedback'].includes(candidate.source) &&
    (candidate.profile_digest === null || typeof candidate.profile_digest === 'string') &&
    candidate.schema_version === 'personality-v1' &&
    Array.isArray(candidate.preferences)
  );
}

export function proposalTaskId(response: ExplicitFeedbackResponse): string | null {
  const task = response.proposal_task;
  if (!task || task.kind !== 'personality.profile.apply') return null;
  return task.id;
}
