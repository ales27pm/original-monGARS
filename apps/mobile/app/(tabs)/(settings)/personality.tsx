import { Alert, ActivityIndicator, Pressable, Share, Text, View } from 'react-native';

import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import {
  useDeletePersonality,
  usePersonalityExport,
  usePersonalityProfile,
  usePersonalityRevisions,
  useResetPersonality,
} from '@/hooks/use-adaptation';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useMongars } from '@/providers/mongars-provider';
import type {
  PersonalityDimension,
  PersonalityPreference,
  PersonalityRevision,
} from '@/types/adaptation';

const DIMENSION_LABELS: Record<PersonalityDimension, string> = {
  brevity: 'Brevity',
  directness: 'Directness',
  formality: 'Formality',
  humor: 'Humor',
  initiative: 'Initiative',
  technical_depth: 'Technical depth',
};

export default function PersonalityScreen() {
  const { client, configurationError } = useMongars();
  const theme = useAppTheme();

  if (!client) {
    return (
      <ScreenScroll>
        <SurfaceCard tone="warning" title="Connect monGARS in Settings">
          <Text selectable style={{ color: theme.warning, fontSize: 14, lineHeight: 20 }}>
            {configurationError?.message ?? 'The local API address is not configured.'}
          </Text>
        </SurfaceCard>
      </ScreenScroll>
    );
  }

  return <ConnectedPersonalityScreen />;
}

function ConnectedPersonalityScreen() {
  const theme = useAppTheme();
  const profile = usePersonalityProfile({ auto: true });
  const revisions = usePersonalityRevisions({ auto: true, limit: 50 });
  const exportQuery = usePersonalityExport({ auto: false });
  const reset = useResetPersonality();
  const deletion = useDeletePersonality();

  const busy =
    profile.isLoading ||
    revisions.isLoading ||
    exportQuery.isLoading ||
    reset.isPending ||
    deletion.isPending;

  async function refreshAll() {
    await Promise.all([profile.refresh(), revisions.refresh()]);
  }

  async function exportProfile() {
    try {
      const payload = await exportQuery.refresh();
      await Share.share({
        message: JSON.stringify(payload, null, 2),
        title: `monGARS personality revision ${payload.current.revision}`,
      });
    } catch {
      // The query exposes a user-readable error below.
    }
  }

  function confirmReset() {
    Alert.alert(
      'Reset response profile?',
      'This removes the active reviewed preferences and revision history, then creates a fresh default profile. Explicit feedback records remain stored.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: () => {
            void reset
              .mutate(undefined)
              .then(refreshAll)
              .catch(() => undefined);
          },
        },
      ],
    );
  }

  function confirmDelete() {
    Alert.alert(
      'Delete personality data?',
      'This removes the active profile, feedback records, and revision history for this owner. This action cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => {
            void deletion
              .mutate(undefined)
              .then(refreshAll)
              .catch(() => undefined);
          },
        },
      ],
    );
  }

  return (
    <ScreenScroll>
      <SurfaceCard tone="primary">
        <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 12 }}>
          <View style={{ flex: 1, gap: 4 }}>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 12, fontWeight: '700' }}>
              MIMICRY PROFILE
            </Text>
            <Text
              selectable
              style={{
                color: theme.text,
                fontSize: 28,
                fontVariant: ['tabular-nums'],
                fontWeight: '800',
              }}
            >
              Revision {profile.data?.revision ?? 0}
            </Text>
          </View>
          <StatusPill
            label={profile.data?.source === 'default' ? 'Default' : 'Reviewed'}
            tone={profile.data?.source === 'default' ? 'primary' : 'positive'}
          />
        </View>
        <Text selectable style={{ color: theme.textSecondary, fontSize: 12, lineHeight: 18 }}>
          Preferences are advisory wording context only. They never grant tool authority, bypass
          approvals, or change security policy.
        </Text>
      </SurfaceCard>

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        <ActionButton disabled={busy} label="Refresh" onPress={() => void refreshAll()} />
        <ActionButton disabled={busy} label="Export" onPress={() => void exportProfile()} />
        <ActionButton disabled={busy} label="Reset" onPress={confirmReset} warning />
        <ActionButton disabled={busy} label="Delete" onPress={confirmDelete} danger />
      </View>

      {busy ? <ActivityIndicator color={theme.primary} /> : null}
      {profile.error || revisions.error || exportQuery.error || reset.error || deletion.error ? (
        <SurfaceCard tone="danger" title="Personality request failed">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {
              (
                deletion.error ??
                reset.error ??
                exportQuery.error ??
                revisions.error ??
                profile.error
              )?.message
            }
          </Text>
        </SurfaceCard>
      ) : null}

      <SectionHeading
        detail="Only explicitly approved values appear here."
        title="Current response preferences"
      />

      {profile.data?.preferences.length ? (
        <View style={{ gap: 10 }}>
          {profile.data.preferences.map((preference) => (
            <PreferenceCard key={preference.dimension} preference={preference} />
          ))}
        </View>
      ) : (
        <SurfaceCard>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 20 }}>
            No reviewed response-style preferences are active. Use “Adjust style” on a committed
            assistant response, then review and approve the proposal in Tasks.
          </Text>
        </SurfaceCard>
      )}

      {profile.data?.profile_digest ? (
        <SurfaceCard title="Profile integrity">
          <Text
            selectable
            style={{
              color: theme.textSecondary,
              fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace',
              fontSize: 11,
              lineHeight: 17,
            }}
          >
            {profile.data.profile_digest}
          </Text>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
            Schema: {profile.data.schema_version}
          </Text>
        </SurfaceCard>
      ) : null}

      <SectionHeading
        detail="Every approved transition remains attributable to its feedback and task."
        title="Immutable revision history"
      />

      {revisions.data?.length ? (
        <View style={{ gap: 10 }}>
          {revisions.data.map((revision) => (
            <RevisionCard key={`${revision.task_id}-${revision.snapshot.revision}`} revision={revision} />
          ))}
        </View>
      ) : (
        <SurfaceCard>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 14 }}>
            No personality revisions have been approved.
          </Text>
        </SurfaceCard>
      )}
    </ScreenScroll>
  );
}

function PreferenceCard({ preference }: { preference: PersonalityPreference }) {
  const theme = useAppTheme();
  return (
    <SurfaceCard>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <View style={{ flex: 1, gap: 4 }}>
          <Text selectable style={{ color: theme.text, fontSize: 16, fontWeight: '700' }}>
            {DIMENSION_LABELS[preference.dimension]}
          </Text>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 12 }}>
            Confidence {Math.round(preference.confidence * 100)}% · {preference.evidence_count}{' '}
            explicit observation{preference.evidence_count === 1 ? '' : 's'}
          </Text>
        </View>
        <Text
          selectable
          style={{
            color: theme.primary,
            fontSize: 22,
            fontVariant: ['tabular-nums'],
            fontWeight: '800',
          }}
        >
          {Math.round(preference.value * 100)}%
        </Text>
      </View>
      <View
        style={{
          backgroundColor: theme.surfaceMuted,
          borderRadius: 999,
          height: 8,
          overflow: 'hidden',
        }}
      >
        <View
          style={{
            backgroundColor: theme.primary,
            borderRadius: 999,
            height: 8,
            width: `${Math.round(preference.value * 100)}%`,
          }}
        />
      </View>
    </SurfaceCard>
  );
}

function RevisionCard({ revision }: { revision: PersonalityRevision }) {
  const theme = useAppTheme();
  const preference = revision.snapshot.preferences.find(
    (item) => item.dimension === revision.changed_dimension,
  );
  return (
    <SurfaceCard tone={revision.conflict ? 'warning' : undefined}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <View style={{ flex: 1, gap: 3 }}>
          <Text selectable style={{ color: theme.text, fontSize: 15, fontWeight: '700' }}>
            Revision {revision.snapshot.revision} · {DIMENSION_LABELS[revision.changed_dimension]}
          </Text>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 12 }}>
            {new Date(revision.created_at).toLocaleString()}
          </Text>
        </View>
        {preference ? (
          <StatusPill label={`${Math.round(preference.value * 100)}%`} tone="primary" />
        ) : null}
      </View>
      <Text
        selectable
        style={{
          color: theme.textTertiary,
          fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace',
          fontSize: 10,
          lineHeight: 15,
        }}
      >
        Task {revision.task_id}
      </Text>
      {revision.conflict ? (
        <Text selectable style={{ color: theme.warning, fontSize: 11 }}>
          This revision replaced a different explicit value after protected review.
        </Text>
      ) : null}
    </SurfaceCard>
  );
}

function ActionButton({
  danger = false,
  disabled,
  label,
  onPress,
  warning = false,
}: {
  danger?: boolean;
  disabled: boolean;
  label: string;
  onPress: () => void;
  warning?: boolean;
}) {
  const theme = useAppTheme();
  const color = danger ? theme.danger : warning ? theme.warning : theme.primary;
  const background = danger
    ? theme.dangerSoft
    : warning
      ? theme.warningSoft
      : theme.primarySoft;

  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => ({
        backgroundColor: background,
        borderColor: color,
        borderRadius: 999,
        borderWidth: 1,
        opacity: disabled ? 0.45 : pressed ? 0.72 : 1,
        paddingHorizontal: 13,
        paddingVertical: 8,
      })}
    >
      <Text style={{ color, fontSize: 12, fontWeight: '700' }}>{label}</Text>
    </Pressable>
  );
}
