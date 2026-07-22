import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';

import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useApproveTask, useTaskDetail, useTasks } from '@/hooks/use-mongars-api';
import { useMongars } from '@/providers/mongars-provider';
import type { TaskResponse } from '@/types/mongars-api';

const taskFilters = ['All', 'Active', 'Approval', 'Done'] as const;
type TaskFilter = (typeof taskFilters)[number];

function matchesFilter(task: TaskResponse, filter: TaskFilter) {
  if (filter === 'All') return true;
  if (filter === 'Active') return ['queued', 'running'].includes(task.status);
  if (filter === 'Approval') return task.status === 'waiting_approval';
  return ['done', 'failed', 'cancelled'].includes(task.status);
}

function statusTone(status: string) {
  if (status === 'done') return 'positive' as const;
  if (status === 'waiting_approval') return 'warning' as const;
  if (['failed', 'cancelled'].includes(status)) return 'danger' as const;
  return 'primary' as const;
}

function progressForStatus(status: string) {
  if (status === 'queued') return 0.15;
  if (status === 'running') return 0.65;
  if (status === 'waiting_approval') return 0.5;
  return 1;
}

export default function TasksScreen() {
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

  return <ConnectedTasksScreen />;
}

function ConnectedTasksScreen() {
  const theme = useAppTheme();
  const query = useTasks({ auto: true, limit: 50 });
  const approval = useApproveTask();
  const [filter, setFilter] = useState<TaskFilter>('All');
  const [reviewTaskId, setReviewTaskId] = useState<string | null>(null);
  const detail = useTaskDetail(reviewTaskId ?? '', { auto: reviewTaskId !== null });
  const tasks = useMemo(
    () => (query.data ?? []).filter((task) => matchesFilter(task, filter)),
    [filter, query.data],
  );
  const activeCount = (query.data ?? []).filter((task) =>
    ['queued', 'running'].includes(task.status),
  ).length;

  async function approve(taskId: string) {
    if (
      detail.data?.id !== taskId ||
      detail.data.status !== 'waiting_approval' ||
      !detail.data.action_digest
    ) {
      return;
    }
    try {
      await approval.mutate(taskId);
      await query.refresh();
      setReviewTaskId(null);
    } catch {
      // The mutation exposes a user-readable error above the task list.
    }
  }

  return (
    <ScreenScroll>
      <SurfaceCard tone="primary">
        <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 12 }}>
          <View style={{ flex: 1, gap: 4 }}>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 12, fontWeight: '600' }}>
              RM QUEUE
            </Text>
            <Text
              selectable
              style={{ color: theme.text, fontSize: 28, fontWeight: '800', fontVariant: ['tabular-nums'] }}
            >
              {activeCount} active
            </Text>
          </View>
          <Pressable accessibilityRole="button" onPress={() => void query.refresh()}>
            {query.isLoading ? (
              <ActivityIndicator color={theme.primary} />
            ) : (
              <StatusPill label="Refresh" tone="primary" />
            )}
          </Pressable>
        </View>
      </SurfaceCard>

      <View style={{ flexDirection: 'row', gap: 8 }}>
        {taskFilters.map((option) => {
          const selected = filter === option;
          return (
            <Pressable
              accessibilityRole="button"
              key={option}
              onPress={() => setFilter(option)}
              style={{
                backgroundColor: selected ? theme.primary : theme.surface,
                borderColor: selected ? theme.primary : theme.border,
                borderRadius: 999,
                borderWidth: 1,
                flex: 1,
                paddingVertical: 8,
              }}
            >
              <Text
                style={{
                  color: selected ? theme.primaryContrast : theme.textSecondary,
                  fontSize: 11,
                  fontWeight: '600',
                  textAlign: 'center',
                }}
              >
                {option}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {query.error || approval.error ? (
        <SurfaceCard tone="danger" title="Task request failed">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {(approval.error ?? query.error)?.message}
          </Text>
        </SurfaceCard>
      ) : null}

      {reviewTaskId ? (
        <SurfaceCard tone="warning" title="Exact approval review">
          {detail.isLoading ? <ActivityIndicator color={theme.warning} /> : null}
          {detail.error ? (
            <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
              {detail.error.message}
            </Text>
          ) : null}
          {detail.data?.id === reviewTaskId ? (
            <>
              <View style={{ gap: 4 }}>
                <Text selectable style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
                  ACTION DIGEST
                </Text>
                <Text
                  selectable
                  style={{
                    color: theme.text,
                    fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace',
                    fontSize: 11,
                    lineHeight: 17,
                  }}
                >
                  {detail.data.action_digest ?? 'Missing digest — approval blocked'}
                </Text>
              </View>
              <View style={{ gap: 4 }}>
                <Text selectable style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
                  EXACT PAYLOAD
                </Text>
                <Text
                  selectable
                  style={{
                    backgroundColor: theme.surface,
                    borderRadius: 12,
                    color: theme.text,
                    fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace',
                    fontSize: 11,
                    lineHeight: 17,
                    padding: 11,
                  }}
                >
                  {JSON.stringify(detail.data.payload, null, 2)}
                </Text>
              </View>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <Pressable
                  accessibilityRole="button"
                  onPress={() => setReviewTaskId(null)}
                  style={{
                    alignItems: 'center',
                    backgroundColor: theme.surface,
                    borderColor: theme.border,
                    borderRadius: 12,
                    borderWidth: 1,
                    flex: 1,
                    padding: 11,
                  }}
                >
                  <Text style={{ color: theme.textSecondary, fontSize: 13, fontWeight: '700' }}>
                    Close
                  </Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  disabled={!detail.data.action_digest || approval.isPending}
                  onPress={() => void approve(reviewTaskId)}
                  style={{
                    alignItems: 'center',
                    backgroundColor: detail.data.action_digest ? theme.warning : theme.surfaceMuted,
                    borderRadius: 12,
                    flex: 1,
                    opacity: approval.isPending ? 0.7 : 1,
                    padding: 11,
                  }}
                >
                  <Text style={{ color: theme.primaryContrast, fontSize: 13, fontWeight: '700' }}>
                    {approval.isPending ? 'Approving…' : 'Approve exact action'}
                  </Text>
                </Pressable>
              </View>
            </>
          ) : null}
        </SurfaceCard>
      ) : null}

      <SectionHeading detail="Durable local work with explicit approval gates" title="Activity" />

      {tasks.map((task) => {
        const progress = progressForStatus(task.status);
        return (
          <SurfaceCard
            key={task.id}
            eyebrow={new Date(task.updated_at).toLocaleString()}
            title={task.kind.replaceAll('.', ' · ')}
            trailing={
              <StatusPill label={task.status.replaceAll('_', ' ')} tone={statusTone(task.status)} />
            }
          >
            <Text selectable style={{ color: theme.textSecondary, fontSize: 13 }}>
              Trace {task.trace_id.slice(0, 12)} · attempt {task.attempt_count}/{task.max_attempts}
            </Text>
            <View style={{ gap: 6 }}>
              <View
                style={{
                  backgroundColor: theme.surfaceMuted,
                  borderRadius: 999,
                  height: 7,
                  overflow: 'hidden',
                }}
              >
                <View
                  style={{
                    backgroundColor: task.status === 'done' ? theme.positive : theme.primary,
                    borderRadius: 999,
                    height: '100%',
                    width: `${Math.round(progress * 100)}%`,
                  }}
                />
              </View>
            </View>
            {task.error_text ? (
              <Text selectable style={{ color: theme.danger, fontSize: 12, lineHeight: 18 }}>
                {task.error_text}
              </Text>
            ) : null}
            {task.status === 'waiting_approval' ? (
              <Pressable
                accessibilityRole="button"
                disabled={detail.isLoading && reviewTaskId === task.id}
                onPress={() => setReviewTaskId(task.id)}
                style={({ pressed }) => ({
                  alignItems: 'center',
                  backgroundColor: theme.warningSoft,
                  borderColor: theme.warning,
                  borderRadius: 12,
                  borderWidth: 1,
                  opacity: pressed || (detail.isLoading && reviewTaskId === task.id) ? 0.7 : 1,
                  padding: 10,
                })}
              >
                <Text style={{ color: theme.warning, fontSize: 13, fontWeight: '700' }}>
                  {detail.isLoading && reviewTaskId === task.id
                    ? 'Loading exact payload…'
                    : 'Review exact payload'}
                </Text>
              </Pressable>
            ) : null}
          </SurfaceCard>
        );
      })}

      {!query.isLoading && !tasks.length ? (
        <SurfaceCard title="No tasks in this view">
          <Text selectable style={{ color: theme.textSecondary, fontSize: 14 }}>
            Refresh the queue or choose another status filter.
          </Text>
        </SurfaceCard>
      ) : null}
    </ScreenScroll>
  );
}
