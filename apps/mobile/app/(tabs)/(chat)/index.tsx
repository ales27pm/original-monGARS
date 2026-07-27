import * as Haptics from 'expo-haptics';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useReducer, useState } from 'react';
import { Alert, Linking, Pressable, Text, TextInput, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { AppIcon } from '@/components/app-icon';
import { ChatFeedbackControls } from '@/components/chat-feedback-controls';
import { IconButton } from '@/components/icon-button';
import { ScreenScroll } from '@/components/screen-scroll';
import {
  SegmentedControl,
  type SegmentedControlOption,
} from '@/components/segmented-control';
import { SurfaceCard } from '@/components/surface-card';
import { VisualAsset } from '@/components/visual-asset';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useStreamingChat } from '@/hooks/use-mongars-api';
import { useMongars } from '@/providers/mongars-provider';
import type {
  ChatCitation,
  ChatRequest,
  ChatResponse,
  JsonValue,
} from '@/types/mongars-api';
import {
  canTransition,
  nextLabel,
  nextVoiceState,
  type VoiceLoopEvent,
  type VoiceLoopState,
} from '@/lib/voice-state-machine';

const suggestions = ['Summarize my day', 'Search project memory', 'Show active tasks'];

type DisplaySource = { label: string; url?: string };
type ChatDisplayMessage = {
  id: string;
  role: 'assistant' | 'user';
  text: string;
  timestamp: string;
  sources?: DisplaySource[];
};

const webSearchModes = ['off', 'auto', 'required'] as const;
type WebSearchMode = NonNullable<ChatRequest['web_search']>;

const webSearchModeLabels: Record<WebSearchMode, string> = {
  off: 'Off',
  auto: 'Auto',
  required: 'Required',
};
const webSearchOptions: readonly SegmentedControlOption<WebSearchMode>[] = webSearchModes.map(
  (mode) => ({
    accessibilityLabel: `${webSearchModeLabels[mode]} web search`,
    label: webSearchModeLabels[mode],
    value: mode,
  }),
);

function voiceReducer(state: VoiceLoopState, event: VoiceLoopEvent): VoiceLoopState {
  return nextVoiceState(state, event);
}

function normalizeWebSource(source: unknown): DisplaySource | null {
  if (!source || typeof source !== 'object') return null;
  const candidate = source as { title?: unknown; url?: unknown };
  if (typeof candidate.title !== 'string' || typeof candidate.url !== 'string') return null;

  try {
    const parsed = new URL(candidate.url);
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) return null;
    const title = candidate.title.trim();
    return {
      label: title && title !== parsed.hostname ? `${parsed.hostname} · ${title}` : parsed.hostname,
      url: parsed.toString(),
    };
  } catch {
    return null;
  }
}

function sourceFromCitation(citation: ChatCitation): DisplaySource {
  const details = locatorDetails(citation.locator);
  const title = citation.title?.trim();
  const base = title || citationLabel(citation);
  const label = [`[${citation.key}] ${base}`, ...details].join(' · ');
  if (citation.kind !== 'web' || !citation.url) return { label };
  return normalizeWebSource({ title: label, url: citation.url }) ?? { label };
}

function citationLabel(citation: ChatCitation): string {
  if (citation.kind === 'memory') return 'Indexed memory';
  if (citation.kind === 'conversation') return 'Prior conversation';
  if (citation.kind === 'policy') return 'Reviewed response preference';
  return 'Web evidence';
}

function locatorDetails(locator: { [key: string]: JsonValue } | null): string[] {
  if (!locator) return [];
  const details: string[] = [];
  const page = locator.page_number ?? locator.page;
  if (typeof page === 'number' && Number.isFinite(page)) details.push(`page ${page}`);
  const lines = lineRange(locator.line_start, locator.line_end);
  if (lines) details.push(lines);
  const headings = locator.heading_path;
  if (Array.isArray(headings)) {
    const path = headings.filter((value): value is string => typeof value === 'string').join(' › ');
    if (path) details.push(path);
  }
  return details;
}

function lineRange(start: JsonValue | undefined, end: JsonValue | undefined): string | null {
  if (typeof start !== 'number' || !Number.isFinite(start)) return null;
  if (typeof end === 'number' && Number.isFinite(end) && end !== start) {
    return `lines ${start}–${end}`;
  }
  return `line ${start}`;
}

function responseSources(response: ChatResponse): DisplaySource[] | undefined {
  if (response.citations?.length) {
    return response.citations.map(sourceFromCitation);
  }
  const web = Array.isArray(response.sources)
    ? response.sources.map(normalizeWebSource).filter((source): source is DisplaySource => source !== null)
    : [];
  if (response.memory_hits) web.push({ label: `${response.memory_hits} memory hits` });
  return web.length ? web : undefined;
}

export default function ChatScreen() {
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

  return <ConnectedChatScreen />;
}

function ConnectedChatScreen() {
  const theme = useAppTheme();
  const router = useRouter();
  const { hasToken } = useMongars();
  const chat = useStreamingChat();
  const [draft, setDraft] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatDisplayMessage[]>([]);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>('auto');
  const [voiceState, dispatchVoiceEvent] = useReducer(voiceReducer, 'idle');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [continuousVoiceLoop, setContinuousVoiceLoop] = useState(false);

  const dispatchVoiceAction = useCallback(
    (event: VoiceLoopEvent): void => {
      if (!canTransition(voiceState, event)) {
        setVoiceError(`Cannot transition ${voiceState} with ${event}`);
        return;
      }
      setVoiceError(null);
      dispatchVoiceEvent(event);
    },
    [voiceState],
  );

  const primaryVoiceEvent: VoiceLoopEvent = (() => {
    if (voiceState === 'idle') return 'start_push_to_talk';
    if (voiceState === 'requesting_permission') return 'permission_granted';
    if (voiceState === 'listening') return 'stop_recording';
    if (voiceState === 'finalizing') return 'transcription_complete';
    if (voiceState === 'thinking') return 'speak_complete';
    if (voiceState === 'speaking') return continuousVoiceLoop ? 'auto_restart' : 'tts_stopped';
    return 'speak_complete';
  })();

  useEffect(() => {
    if (!continuousVoiceLoop || voiceState !== 'speaking') return;
    const handle = setTimeout(() => dispatchVoiceAction('auto_restart'), 0);
    return () => clearTimeout(handle);
  }, [continuousVoiceLoop, dispatchVoiceAction, voiceState]);

  async function submitMessage() {
    const text = draft.trim();
    if (!text || chat.isPending) return;
    if (process.env.EXPO_OS === 'ios') {
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    }
    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: 'user', text, timestamp: 'Now' },
    ]);
    try {
      const response = await chat.mutate({
        message: text,
        session_id: sessionId,
        require_local_only: true,
        web_search: webSearchMode,
      });
      setSessionId(response.session_id);
      setMessages((current) => [
        ...current,
        {
          id: response.trace_id,
          role: 'assistant',
          text: response.answer,
          timestamp: 'Now',
          sources: responseSources(response),
        },
      ]);
      setDraft((current) => (current.trim() === text ? '' : current));
    } catch {
      // The accepted user turn remains visible. Invalid or cancelled assistant drafts are
      // discarded by the streaming hook and never promoted to a committed message.
    }
  }

  const transientMessage: ChatDisplayMessage | null = chat.isPending
    ? {
        id: 'assistant-streaming',
        role: 'assistant',
        text: chat.draftText || '…',
        timestamp: 'Streaming',
      }
    : null;
  const displayedMessages = transientMessage ? [...messages, transientMessage] : messages;

  return (
    <ScreenScroll>
      <SurfaceCard>
        <View style={{ alignItems: 'center', flexDirection: 'row', gap: 10 }}>
          <View
            style={{
              backgroundColor: hasToken ? theme.positive : theme.warning,
              borderRadius: 999,
              height: 10,
              width: 10,
            }}
          />
          <View style={{ flex: 1, gap: 2 }}>
            <Text selectable style={{ color: theme.text, fontSize: 14, fontWeight: '700' }}>
              Local Cortex
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 11 }}>
              {chat.data?.model ?? 'Private local model'}
            </Text>
          </View>
          <View style={{ alignItems: 'flex-end', gap: 2 }}>
            <Text style={{ color: theme.textTertiary, fontSize: 9 }}>READINESS</Text>
            <Text
              style={{
                color: chat.isPending ? theme.primary : hasToken ? theme.positive : theme.warning,
                fontSize: 11,
                fontWeight: '700',
              }}
            >
              {chat.isPending ? 'Streaming' : hasToken ? 'Ready' : 'Token needed'}
            </Text>
          </View>
          <AppIcon color={theme.textTertiary} name="chevronRight" size={18} />
        </View>
      </SurfaceCard>

      <View style={{ gap: 10 }}>
        {!displayedMessages.length ? (
          <View style={{ alignItems: 'flex-start', flexDirection: 'row', gap: 8 }}>
            <VisualAsset
              accessibilityLabel="Local cortex visual"
              name="cortexEmblem"
              size={42}
            />
            <View
              style={{
                backgroundColor: theme.surface,
                borderColor: theme.border,
                borderRadius: radii.large,
                borderWidth: 1,
                flex: 1,
                gap: 5,
                padding: 12,
              }}
            >
              <Text selectable style={{ color: theme.text, fontSize: 13, fontWeight: '700' }}>
                Private, local conversation
              </Text>
              <Text
                selectable
                style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 18 }}
              >
                Ask Cortex to reason over indexed memory or coordinate a protected local task.
              </Text>
            </View>
          </View>
        ) : null}
        {displayedMessages.map((message) => {
          const isUser = message.role === 'user';
          const isCommittedAssistant = !isUser && message.id !== 'assistant-streaming';
          return (
            <View
              key={message.id}
              style={{
                alignItems: isUser ? 'flex-end' : 'flex-start',
                paddingLeft: isUser ? 38 : 0,
                paddingRight: isUser ? 0 : 26,
                gap: 5,
              }}
            >
              <View
                style={{
                  backgroundColor: isUser ? theme.primarySoft : theme.surface,
                  borderColor: isUser ? '#D9CEF0' : theme.border,
                  borderCurve: 'continuous',
                  borderRadius: radii.large,
                  borderWidth: 1,
                  gap: 8,
                  paddingHorizontal: 13,
                  paddingVertical: 11,
                }}
              >
                <Text
                  selectable
                  style={{
                    color: theme.text,
                    fontSize: 14,
                    lineHeight: 20,
                  }}
                >
                  {message.text}
                </Text>
                {message.sources ? (
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    {message.sources.map((source, index) => (
                      <Pressable
                        accessibilityRole={source.url ? 'link' : 'text'}
                        disabled={!source.url}
                        key={`${source.url ?? source.label}-${index}`}
                        onPress={() =>
                          source.url
                            ? void Linking.openURL(source.url).catch(() => {
                                Alert.alert(
                                  'Could not open web result',
                                  'The source link could not be opened on this device.',
                                );
                              })
                            : undefined
                        }
                        style={({ pressed }) => ({
                          backgroundColor: theme.surfaceMuted,
                          borderRadius: 999,
                          maxWidth: 280,
                          opacity: pressed ? 0.7 : 1,
                          paddingHorizontal: 9,
                          paddingVertical: 4,
                        })}
                      >
                        <Text
                          ellipsizeMode="tail"
                          numberOfLines={1}
                          selectable
                          style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '600' }}
                        >
                          {source.label}
                        </Text>
                      </Pressable>
                    ))}
                  </View>
                ) : null}
              </View>
              <Text
                selectable
                style={{
                  color: theme.textTertiary,
                  fontSize: 11,
                  fontVariant: ['tabular-nums'],
                  paddingHorizontal: 7,
                }}
              >
                {message.timestamp}
              </Text>
              {isCommittedAssistant ? <ChatFeedbackControls traceId={message.id} /> : null}
            </View>
          );
        })}
      </View>

      <View style={{ gap: 7 }}>
        <Text selectable style={{ color: theme.textSecondary, fontSize: 10, fontWeight: '700' }}>
          QUICK START
        </Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {suggestions.map((suggestion) => (
            <AppButton
              key={suggestion}
              label={suggestion}
              onPress={() => setDraft(suggestion)}
              size="compact"
              tone="neutral"
              variant="outline"
            />
          ))}
        </View>
      </View>

      <View
        style={{
          backgroundColor: theme.surface,
          borderColor: theme.border,
          borderCurve: 'continuous',
          borderRadius: radii.large,
          borderWidth: 1,
          gap: 8,
          padding: 10,
        }}
      >
        <TextInput
          accessibilityLabel="Message Cortex"
          maxLength={32_000}
          multiline
          onChangeText={setDraft}
          placeholder="Message Cortex…"
          placeholderTextColor={theme.textTertiary}
          selectionColor={theme.primary}
          style={{
            color: theme.text,
            fontSize: 15,
            lineHeight: 21,
            maxHeight: 126,
            minHeight: 48,
            paddingHorizontal: 5,
            paddingVertical: 5,
            textAlignVertical: 'top',
          }}
          value={draft}
        />
        <View style={{ alignItems: 'center', flexDirection: 'row', gap: 8 }}>
          <AppIcon color={theme.textSecondary} name="globe" size={17} />
          <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '600' }}>
            Web search
          </Text>
          <SegmentedControl
            accessibilityLabel="Web search mode"
            fill={false}
            onChange={setWebSearchMode}
            options={webSearchOptions}
            size="compact"
            value={webSearchMode}
          />
        </View>
        <View
          style={{
            alignItems: 'center',
            borderTopColor: theme.border,
            borderTopWidth: 1,
            flexDirection: 'row',
            gap: 4,
            paddingTop: 7,
          }}
        >
          <IconButton
            accessibilityLabel="Open document memory"
            icon="paperclip"
            onPress={() => router.navigate('/(tabs)/(memory)')}
            size="compact"
          />
          <IconButton
            accessibilityLabel={`Voice: ${nextLabel(voiceState)}`}
            icon="microphone"
            onPress={() => dispatchVoiceAction(primaryVoiceEvent)}
            selected={voiceState !== 'idle'}
            size="compact"
            tone="primary"
            variant="soft"
          />
          <AppButton
            label={continuousVoiceLoop ? 'Loop on' : 'Loop off'}
            onPress={() => setContinuousVoiceLoop((enabled) => !enabled)}
            size="compact"
            tone={continuousVoiceLoop ? 'primary' : 'neutral'}
            variant="soft"
          />
          <Text style={{ color: theme.textTertiary, flex: 1, fontSize: 10 }}>
            Local inference
          </Text>
          <IconButton
            accessibilityLabel={chat.isPending ? 'Cancel response' : 'Send message'}
            disabled={!chat.isPending && !draft.trim()}
            icon={chat.isPending ? 'close' : 'send'}
            onPress={() => {
              if (chat.isPending) chat.cancel();
              else void submitMessage();
            }}
            size="compact"
            tone={chat.isPending ? 'danger' : 'primary'}
            variant="solid"
          />
        </View>
        {voiceState !== 'idle' || voiceError ? (
          <View
            style={{
              alignItems: 'center',
              backgroundColor: theme.surfaceMuted,
              borderRadius: radii.small,
              flexDirection: 'row',
              gap: 8,
              paddingHorizontal: 9,
              paddingVertical: 7,
            }}
          >
            <Text style={{ color: voiceError ? theme.warning : theme.textSecondary, flex: 1, fontSize: 10 }}>
              {voiceError ?? `Voice ${nextLabel(voiceState)} · no raw audio is persisted`}
            </Text>
            <AppButton
              label="Cancel"
              onPress={() => dispatchVoiceAction('cancel')}
              size="compact"
              tone="neutral"
              variant="outline"
            />
          </View>
        ) : null}
      </View>
      {chat.error ? (
        <SurfaceCard tone="danger" title="Response interrupted">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {chat.error.message}
          </Text>
        </SurfaceCard>
      ) : null}
    </ScreenScroll>
  );
}
