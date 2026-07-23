import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, Share, Text, View } from 'react-native';

import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useMongars } from '@/providers/mongars-provider';
import type {
  PersonalityLifecycleEventResponse,
  PersonalityProfileResponse,
  PersonalityRevisionResponse,
  TaskResponse,
} from '@/types/mongars-api';

type BusyAction = 'delete' | 'export' | 'load' | 'reset' | null;

function humanize(value: string) {
  return value.replaceAll('_', ' ');
}

export function PersonalityControls() {
  const theme = useAppTheme();
  const { client, configurationError, hasToken } = useMongars();
  const [profile, setProfile] = useState<PersonalityProfileResponse | null>(null);
  const [revisions, setRevisions] = useState<PersonalityRevisionResponse[]>([]);
  const [lifecycle, setLifecycle] = useState<PersonalityLifecycleEventResponse[]>([]);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastTask, setLastTask] = useState<TaskResponse | null>(null);
  const [exportText, setExportText] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!client || !hasToken) return;
      setBusy('load');
      setError(null);
      try {
        const [nextProfile, nextRevisions, nextLifecycle] = await Promise.all([
          client.getPersonalityProfile({ signal }),
          client.getPersonalityRevisions(100, { signal }),
          client.getPersonalityLifecycle(100, { signal }),
        ]);
        setProfile(nextProfile);
        setRevisions(nextRevisions);
        setLifecycle(nextLifecycle);
      } catch (requestError) {
        if (!(requestError instanceof Error && requestError.name === 'AbortError')) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Unable to load personality data.',
          );
        }
      } finally {
        setBusy((current) => (current === 'load' ? null : current));
      }
    },
    [client, hasToken],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function createLifecycleTask(action: 'delete' | 'reset') {
    if (!client || busy) return;
    setBusy(action);
    setError(null);
    setLastTask(null);
    try {
      const task =
        action === 'reset'
          ? await client.requestPersonalityReset()
          : await client.requestPersonalityDelete();
      setLastTask(task);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : `Unable to prepare personality ${action}.`,
      );
    } finally {
      setBusy(null);
    }
  }

  async function exportProfile() {
    if (!client || busy) return;
    setBusy('export');
    setError(null);
    try {
      const payload = await client.exportPersonalityProfile();
      const rendered = JSON.stringify(payload, null, 2);
      setExportText(
        rendered.length <= 16_000
          ? rendered
          : `${rendered.slice(0, 16_000)}\n… export truncated in this view …`,
      );
      await Share.share({
        message: rendered,
        title: `monGARS personality revision ${payload.profile.revision}`,
      });
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Unable to export personality data.',
      );
    } finally {
      setBusy(null);
    }
  }

  function confirmDelete() {
    Alert.alert(
      'Prepare personality deletion?',
      'This creates an approval task that removes profile state, private feedback, history, and old personality task payloads. Nothing is deleted until you approve the exact payload in Tasks.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Prepare deletion',
          style: 'destructive',
          onPress: () => void createLifecycleTask('delete'),
        },
      ],
    );
  }

  if (!client || !hasToken) {
    return (
      <>
        <SectionHeading
          detail="Connect an authenticated control plane before inspecting owner-scoped adaptation data."
          title="Personality"
        />
        <SurfaceCard tone="warning" title="Connection required">
          <Text selectable style={{ color: theme.warning, fontSize: 13, lineHeight: 19 }}>
            {configurationError?.message ?? 'Configure the server and API token in Settings.'}
          </Text>
        </SurfaceCard>
      </>
    );
  }

  const preferences = profile?.preferences ?? [];
  return (
    <>
      <SectionHeading
        detail="Inspect the immutable advisory snapshot and create exact-payload lifecycle tasks."
        title="Personality"
      />

      <SurfaceCard tone="primary">
        <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 12 }}>
          <View style={{ flex: 1, gap: 4 }}>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 12, fontWeight: '700' }}>
              CURRENT SNAPSHOT
            </Text>
            <Text
              selectable
              style={{ color: theme.text, fontSize: 28, fontWeight: '800', fontVariant: ['tabular-nums'] }}
            >
              Revision {profile?.revision ?? '—'}
            </Text>
          </View>
          <StatusPill
            label={profile ? humanize(profile.source) : 'Loading'}
            tone={profile?.source === 'default' ? 'warning' : 'positive'}
          />
        </View>
        <Text
          selectable
          style={{ color: theme.textTertiary, fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 10, lineHeight: 15 }}
        >
          {profile?.profile_digest ?? 'Default snapshot has no persisted digest'}
        </Text>
      </SurfaceCard>

      {busy === 'load' && !profile ? <ActivityIndicator color={theme.primary} /> : null}

      <SurfaceCard title="Active preferences">
        {preferences.length ? (
          preferences.map((preference) => (
            <View
              key={preference.dimension}
              style={{ borderBottomColor: theme.border, borderBottomWidth: 1, gap: 7, paddingVertical: 8 }}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <Text selectable style={{ color: theme.text, flex: 1, fontSize: 14, fontWeight: '700' }}>
                  {humanize(preference.dimension)}
                </Text>
                <Text
                  selectable
                  style={{ color: theme.primary, fontSize: 14, fontVariant: ['tabular-nums'], fontWeight: '800' }}
                >
                  {preference.value.toFixed(2)}
                </Text>
              </View>
              <View style={{ backgroundColor: theme.surfaceMuted, borderRadius: 999, height: 7, overflow: 'hidden' }}>
                <View
                  style={{ backgroundColor: theme.primary, borderRadius: 999, height: 7, width: `${Math.max(0, Math.min(100, preference.value * 100))}%` }}
                />
              </View>
              <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
                Confidence {preference.confidence.toFixed(2)} · {preference.evidence_count} evidence
              </Text>
            </View>
          ))
        ) : (
          <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 19 }}>
            No active style preferences. Cortex uses neutral defaults.
          </Text>
        )}
      </SurfaceCard>

      <SurfaceCard title="Audit history">
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <StatusPill label={`${revisions.length} preference revisions`} tone="primary" />
          <StatusPill label={`${lifecycle.length} lifecycle receipts`} tone="primary" />
        </View>
        {lifecycle.slice(0, 5).map((event) => (
          <Text
            key={`${event.task_id}-${event.created_at}`}
            selectable
            style={{ color: theme.textSecondary, fontSize: 12, lineHeight: 18 }}
          >
            {humanize(event.operation)} · revision {event.expected_revision} → {event.target_revision}
          </Text>
        ))}
      </SurfaceCard>

      <SurfaceCard title="Owner controls">
        <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 19 }}>
          Reset and deletion create local-mutation tasks. Review every exact payload page in Tasks before approval.
        </Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <ActionButton
            disabled={busy !== null}
            label={busy === 'export' ? 'Exporting…' : 'Export JSON'}
            onPress={() => void exportProfile()}
            tone="primary"
          />
          <ActionButton
            disabled={busy !== null || preferences.length === 0}
            label={busy === 'reset' ? 'Preparing…' : 'Prepare reset'}
            onPress={() => void createLifecycleTask('reset')}
            tone="warning"
          />
          <ActionButton
            disabled={busy !== null}
            label={busy === 'delete' ? 'Preparing…' : 'Prepare deletion'}
            onPress={confirmDelete}
            tone="danger"
          />
          <ActionButton
            disabled={busy !== null}
            label="Refresh"
            onPress={() => void load()}
            tone="secondary"
          />
        </View>
      </SurfaceCard>

      {lastTask ? (
        <SurfaceCard tone="warning" title="Protected task created">
          <Text selectable style={{ color: theme.warning, fontSize: 13, lineHeight: 19 }}>
            {humanize(lastTask.kind)} is {humanize(lastTask.status)}. Open Tasks and review the exact payload before approval.
          </Text>
          <Text
            selectable
            style={{ color: theme.text, fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 11 }}
          >
            {lastTask.id}
          </Text>
        </SurfaceCard>
      ) : null}

      {exportText ? (
        <SurfaceCard title="Private export preview">
          <Text
            selectable
            style={{ color: theme.textSecondary, fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 10, lineHeight: 15 }}
          >
            {exportText}
          </Text>
        </SurfaceCard>
      ) : null}

      {error ? (
        <SurfaceCard tone="danger" title="Personality request failed">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {error}
          </Text>
        </SurfaceCard>
      ) : null}
    </>
  );
}

type ActionButtonProps = {
  disabled: boolean;
  label: string;
  onPress: () => void;
  tone: 'danger' | 'primary' | 'secondary' | 'warning';
};

function ActionButton({ disabled, label, onPress, tone }: ActionButtonProps) {
  const theme = useAppTheme();
  const palette = {
    danger: { background: theme.dangerSoft, foreground: theme.danger },
    primary: { background: theme.primarySoft, foreground: theme.primary },
    secondary: { background: theme.surfaceMuted, foreground: theme.textSecondary },
    warning: { background: theme.warningSoft, foreground: theme.warning },
  }[tone];
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => ({
        backgroundColor: palette.background,
        borderRadius: 12,
        opacity: disabled ? 0.45 : pressed ? 0.7 : 1,
        paddingHorizontal: 13,
        paddingVertical: 10,
      })}
    >
      <Text style={{ color: palette.foreground, fontSize: 12, fontWeight: '800' }}>{label}</Text>
    </Pressable>
  );
}
