import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
} from 'react-native';

import { radii } from '@/constants/theme';
import { useSubmitFeedback } from '@/hooks/use-adaptation';
import { useAppTheme } from '@/hooks/use-app-theme';
import { createFeedbackId } from '@/lib/feedback-id';
import type {
  ExplicitFeedbackResponse,
  PersonalityDimension,
} from '@/types/adaptation';

type FeedbackStatus = 'accepted' | 'failed' | 'idle' | 'submitting';

type HelpfulnessAttempt = {
  feedbackId: string;
  helpful: boolean;
};

type CorrectionAttempt = {
  feedbackId: string;
  text: string;
};

type PreferenceAttempt = {
  feedbackId: string;
  dimension: PersonalityDimension;
  value: number;
};

type Props = {
  traceId: string;
};

const PREFERENCE_DIMENSIONS: readonly {
  dimension: PersonalityDimension;
  label: string;
}[] = [
  { dimension: 'brevity', label: 'Brevity' },
  { dimension: 'directness', label: 'Directness' },
  { dimension: 'formality', label: 'Formality' },
  { dimension: 'humor', label: 'Humor' },
  { dimension: 'initiative', label: 'Initiative' },
  { dimension: 'technical_depth', label: 'Technical depth' },
];

const PREFERENCE_VALUES = [0, 0.25, 0.5, 0.75, 1] as const;

function feedbackStatus(
  accepted: boolean,
  pending: boolean,
  failed: boolean,
): FeedbackStatus {
  if (accepted) return 'accepted';
  if (pending) return 'submitting';
  if (failed) return 'failed';
  return 'idle';
}

export function ChatFeedbackControls({ traceId }: Props) {
  const theme = useAppTheme();
  const router = useRouter();
  const helpfulness = useSubmitFeedback();
  const correction = useSubmitFeedback();
  const preference = useSubmitFeedback();

  const [helpfulnessAttempt, setHelpfulnessAttempt] =
    useState<HelpfulnessAttempt | null>(null);
  const [acceptedHelpfulness, setAcceptedHelpfulness] = useState<boolean | null>(null);

  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [correctionText, setCorrectionText] = useState('');
  const [correctionAttempt, setCorrectionAttempt] = useState<CorrectionAttempt | null>(null);
  const [correctionAccepted, setCorrectionAccepted] = useState(false);

  const [preferenceOpen, setPreferenceOpen] = useState(false);
  const [preferenceDimension, setPreferenceDimension] =
    useState<PersonalityDimension>('technical_depth');
  const [preferenceValue, setPreferenceValue] = useState<number>(0.75);
  const [preferenceAttempt, setPreferenceAttempt] = useState<PreferenceAttempt | null>(null);
  const [preferenceReceipt, setPreferenceReceipt] =
    useState<ExplicitFeedbackResponse | null>(null);

  const proposalTaskId = preferenceReceipt?.proposal_task?.id ?? null;
  const helpfulnessState = feedbackStatus(
    acceptedHelpfulness !== null,
    helpfulness.isPending,
    helpfulness.error !== null,
  );
  const correctionState = feedbackStatus(
    correctionAccepted,
    correction.isPending,
    correction.error !== null,
  );

  const anyPending = helpfulness.isPending || correction.isPending || preference.isPending;

  async function submitHelpfulness(helpful: boolean) {
    if (anyPending || acceptedHelpfulness !== null) return;
    const attempt =
      helpfulnessAttempt?.helpful === helpful
        ? helpfulnessAttempt
        : { feedbackId: createFeedbackId(), helpful };
    setHelpfulnessAttempt(attempt);

    try {
      await helpfulness.mutate({
        kind: 'helpfulness',
        feedback_id: attempt.feedbackId,
        response_trace_id: traceId,
        helpful,
      });
      setAcceptedHelpfulness(helpful);
      setHelpfulnessAttempt(null);
    } catch {
      // Preserve the feedback UUID so a retry remains idempotent.
    }
  }

  function openCorrection() {
    correction.reset();
    setCorrectionOpen(true);
  }

  async function submitCorrection() {
    const normalized = correctionText.trim();
    if (!normalized || correction.isPending) return;
    const attempt =
      correctionAttempt?.text === normalized
        ? correctionAttempt
        : { feedbackId: createFeedbackId(), text: normalized };
    setCorrectionAttempt(attempt);

    try {
      await correction.mutate({
        kind: 'correction',
        feedback_id: attempt.feedbackId,
        response_trace_id: traceId,
        correction_text: normalized,
      });
      setCorrectionAccepted(true);
      setCorrectionOpen(false);
      setCorrectionText('');
      setCorrectionAttempt(null);
    } catch {
      // Retain the private draft and UUID for an explicit retry.
    }
  }

  function openPreference() {
    preference.reset();
    setPreferenceOpen(true);
  }

  async function submitPreference() {
    if (preference.isPending) return;
    const attempt =
      preferenceAttempt?.dimension === preferenceDimension &&
      preferenceAttempt.value === preferenceValue
        ? preferenceAttempt
        : {
            feedbackId: createFeedbackId(),
            dimension: preferenceDimension,
            value: preferenceValue,
          };
    setPreferenceAttempt(attempt);

    try {
      const receipt = await preference.mutate({
        kind: 'preference',
        feedback_id: attempt.feedbackId,
        response_trace_id: traceId,
        dimension: attempt.dimension,
        desired_value: attempt.value,
      });
      setPreferenceReceipt(receipt);
      setPreferenceOpen(false);
      setPreferenceAttempt(null);
    } catch {
      // Preserve the UUID for an exact retry of the same proposal.
    }
  }

  function changeCorrectionText(value: string) {
    setCorrectionText(value);
    if (correctionAttempt && correctionAttempt.text !== value.trim()) {
      setCorrectionAttempt(null);
      correction.reset();
    }
  }

  function changePreferenceDimension(dimension: PersonalityDimension) {
    setPreferenceDimension(dimension);
    if (preferenceAttempt?.dimension !== dimension) {
      setPreferenceAttempt(null);
      preference.reset();
    }
  }

  function changePreferenceValue(value: number) {
    setPreferenceValue(value);
    if (preferenceAttempt?.value !== value) {
      setPreferenceAttempt(null);
      preference.reset();
    }
  }

  const acceptedLabel = useMemo(() => {
    if (acceptedHelpfulness === true) return 'Helpful feedback recorded';
    if (acceptedHelpfulness === false) return 'Not-helpful feedback recorded';
    return null;
  }, [acceptedHelpfulness]);

  return (
    <>
      <View
        accessibilityLabel="Response feedback"
        style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}
      >
        {acceptedLabel ? (
          <FeedbackPill label={acceptedLabel} />
        ) : (
          <>
            <FeedbackButton
              disabled={anyPending}
              label={helpfulnessAttempt?.helpful === true ? 'Retry helpful' : 'Helpful'}
              onPress={() => void submitHelpfulness(true)}
            />
            <FeedbackButton
              disabled={anyPending}
              label={helpfulnessAttempt?.helpful === false ? 'Retry not helpful' : 'Not helpful'}
              onPress={() => void submitHelpfulness(false)}
            />
          </>
        )}
        <FeedbackButton
          disabled={anyPending || correctionAccepted}
          label={correctionAccepted ? 'Correction recorded' : 'Correct answer'}
          onPress={openCorrection}
        />
        <FeedbackButton
          disabled={anyPending}
          label={preferenceReceipt ? 'Style proposal created' : 'Adjust style'}
          onPress={openPreference}
        />
        <FeedbackButton
          disabled={anyPending}
          label="View profile"
          onPress={() => router.push('/(tabs)/(settings)/personality')}
        />
      </View>

      {helpfulnessState === 'submitting' ? (
        <InlineStatus label="Saving feedback…" />
      ) : helpfulnessState === 'failed' ? (
        <InlineStatus danger label={helpfulness.error?.message ?? 'Feedback failed.'} />
      ) : null}
      {correctionState === 'failed' && !correctionOpen ? (
        <InlineStatus danger label={correction.error?.message ?? 'Correction failed.'} />
      ) : null}
      {proposalTaskId ? (
        <View style={{ gap: 6 }}>
          <Text selectable style={{ color: theme.warning, fontSize: 11, lineHeight: 16 }}>
            Style changes are not active yet. Review task {proposalTaskId} before approval.
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/(tabs)/(tasks)')}
            style={({ pressed }) => ({
              alignSelf: 'flex-start',
              backgroundColor: theme.warningSoft,
              borderColor: theme.warning,
              borderRadius: 999,
              borderWidth: 1,
              opacity: pressed ? 0.72 : 1,
              paddingHorizontal: 10,
              paddingVertical: 6,
            })}
          >
            <Text style={{ color: theme.warning, fontSize: 11, fontWeight: '700' }}>
              Review in Tasks
            </Text>
          </Pressable>
        </View>
      ) : null}

      <Modal
        animationType="slide"
        onRequestClose={() => setCorrectionOpen(false)}
        presentationStyle="pageSheet"
        visible={correctionOpen}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          style={{ backgroundColor: theme.background, flex: 1 }}
        >
          <View style={{ flex: 1, gap: 16, padding: 20, paddingTop: 30 }}>
            <Text style={{ color: theme.text, fontSize: 24, fontWeight: '800' }}>
              Correct this answer
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 19 }}>
              The correction stays in the private feedback record. It is not copied into the chat
              transcript, personality revisions, or autobiographical event payloads.
            </Text>
            <TextInput
              accessibilityLabel="Corrected answer"
              autoCorrect
              maxLength={2_000}
              multiline
              onChangeText={changeCorrectionText}
              placeholder="Write the corrected answer…"
              placeholderTextColor={theme.textTertiary}
              selectionColor={theme.primary}
              style={{
                backgroundColor: theme.input,
                borderColor: theme.border,
                borderRadius: radii.large,
                borderWidth: 1,
                color: theme.text,
                flex: 1,
                fontSize: 15,
                lineHeight: 21,
                minHeight: 180,
                padding: 14,
                textAlignVertical: 'top',
              }}
              value={correctionText}
            />
            {correction.error ? (
              <InlineStatus danger label={correction.error.message} />
            ) : null}
            <ModalActions
              disabled={!correctionText.trim() || correction.isPending}
              pending={correction.isPending}
              primaryLabel={correction.error ? 'Retry correction' : 'Submit correction'}
              onCancel={() => setCorrectionOpen(false)}
              onSubmit={() => void submitCorrection()}
            />
          </View>
        </KeyboardAvoidingView>
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => setPreferenceOpen(false)}
        presentationStyle="pageSheet"
        visible={preferenceOpen}
      >
        <View
          style={{
            backgroundColor: theme.background,
            flex: 1,
            gap: 18,
            padding: 20,
            paddingTop: 30,
          }}
        >
          <Text style={{ color: theme.text, fontSize: 24, fontWeight: '800' }}>
            Adjust response style
          </Text>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 19 }}>
            This creates a reviewable local-mutation task. The active profile does not change until
            you inspect the exact payload and approve it in Tasks.
          </Text>
          <View style={{ gap: 8 }}>
            <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
              DIMENSION
            </Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {PREFERENCE_DIMENSIONS.map((item) => (
                <ChoicePill
                  key={item.dimension}
                  label={item.label}
                  onPress={() => changePreferenceDimension(item.dimension)}
                  selected={preferenceDimension === item.dimension}
                />
              ))}
            </View>
          </View>
          <View style={{ gap: 8 }}>
            <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
              DESIRED LEVEL
            </Text>
            <View style={{ flexDirection: 'row', gap: 7 }}>
              {PREFERENCE_VALUES.map((value) => (
                <ChoicePill
                  compact
                  key={value}
                  label={`${Math.round(value * 100)}%`}
                  onPress={() => changePreferenceValue(value)}
                  selected={preferenceValue === value}
                />
              ))}
            </View>
          </View>
          <View
            style={{
              backgroundColor: theme.surface,
              borderColor: theme.border,
              borderRadius: radii.large,
              borderWidth: 1,
              gap: 5,
              padding: 14,
            }}
          >
            <Text style={{ color: theme.text, fontSize: 15, fontWeight: '700' }}>
              {PREFERENCE_DIMENSIONS.find((item) => item.dimension === preferenceDimension)?.label}
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 13 }}>
              Proposed value: {preferenceValue.toFixed(2)} on the reviewed 0–1 scale.
            </Text>
          </View>
          {preference.error ? <InlineStatus danger label={preference.error.message} /> : null}
          <View style={{ flex: 1 }} />
          <ModalActions
            disabled={preference.isPending}
            pending={preference.isPending}
            primaryLabel={preference.error ? 'Retry proposal' : 'Create proposal'}
            onCancel={() => setPreferenceOpen(false)}
            onSubmit={() => void submitPreference()}
          />
        </View>
      </Modal>
    </>
  );
}

function FeedbackButton({
  disabled,
  label,
  onPress,
}: {
  disabled: boolean;
  label: string;
  onPress: () => void;
}) {
  const theme = useAppTheme();
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => ({
        backgroundColor: theme.surfaceMuted,
        borderColor: theme.border,
        borderRadius: 999,
        borderWidth: 1,
        opacity: disabled ? 0.45 : pressed ? 0.72 : 1,
        paddingHorizontal: 9,
        paddingVertical: 5,
      })}
    >
      <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>{label}</Text>
    </Pressable>
  );
}

function FeedbackPill({ label }: { label: string }) {
  const theme = useAppTheme();
  return (
    <View
      style={{
        backgroundColor: theme.positiveSoft,
        borderRadius: 999,
        paddingHorizontal: 9,
        paddingVertical: 5,
      }}
    >
      <Text style={{ color: theme.positive, fontSize: 11, fontWeight: '700' }}>{label}</Text>
    </View>
  );
}

function ChoicePill({
  compact = false,
  label,
  onPress,
  selected,
}: {
  compact?: boolean;
  label: string;
  onPress: () => void;
  selected: boolean;
}) {
  const theme = useAppTheme();
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={onPress}
      style={({ pressed }) => ({
        alignItems: 'center',
        backgroundColor: selected ? theme.primary : theme.surface,
        borderColor: selected ? theme.primary : theme.border,
        borderRadius: 999,
        borderWidth: 1,
        flex: compact ? 1 : undefined,
        opacity: pressed ? 0.75 : 1,
        paddingHorizontal: compact ? 7 : 11,
        paddingVertical: 8,
      })}
    >
      <Text
        style={{
          color: selected ? theme.primaryContrast : theme.textSecondary,
          fontSize: 11,
          fontWeight: '700',
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function InlineStatus({ danger = false, label }: { danger?: boolean; label: string }) {
  const theme = useAppTheme();
  return (
    <Text
      selectable
      style={{
        color: danger ? theme.danger : theme.textTertiary,
        fontSize: 11,
        lineHeight: 16,
      }}
    >
      {label}
    </Text>
  );
}

function ModalActions({
  disabled,
  onCancel,
  onSubmit,
  pending,
  primaryLabel,
}: {
  disabled: boolean;
  onCancel: () => void;
  onSubmit: () => void;
  pending: boolean;
  primaryLabel: string;
}) {
  const theme = useAppTheme();
  return (
    <View style={{ flexDirection: 'row', gap: 10 }}>
      <Pressable
        accessibilityRole="button"
        onPress={onCancel}
        style={({ pressed }) => ({
          alignItems: 'center',
          backgroundColor: theme.surface,
          borderColor: theme.border,
          borderRadius: 14,
          borderWidth: 1,
          flex: 1,
          opacity: pressed ? 0.72 : 1,
          padding: 13,
        })}
      >
        <Text style={{ color: theme.textSecondary, fontSize: 14, fontWeight: '700' }}>Cancel</Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        disabled={disabled}
        onPress={onSubmit}
        style={({ pressed }) => ({
          alignItems: 'center',
          backgroundColor: disabled ? theme.surfaceMuted : theme.primary,
          borderRadius: 14,
          flex: 1,
          opacity: disabled ? 0.5 : pressed ? 0.72 : 1,
          padding: 13,
        })}
      >
        {pending ? (
          <ActivityIndicator color={theme.primaryContrast} />
        ) : (
          <Text
            style={{
              color: disabled ? theme.textTertiary : theme.primaryContrast,
              fontSize: 14,
              fontWeight: '700',
            }}
          >
            {primaryLabel}
          </Text>
        )}
      </Pressable>
    </View>
  );
}
