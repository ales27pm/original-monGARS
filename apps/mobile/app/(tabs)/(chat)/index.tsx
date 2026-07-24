import * as Haptics from 'expo-haptics';
import { useEffect, useReducer, useState } from 'react';
import { Alert, Linking, Pressable, Text, TextInput, View } from 'react-native';

import { BrandMark } from '@/components/brand-mark';
import { ScreenScroll } from '@/components/screen-scroll';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useStreamingChat } from '@/hooks/use-streaming-chat';
import {
  canTransition,
  nextLabel,
  nextVoiceState,
  type VoiceLoopEvent,
  type VoiceLoopState,
} from '@/lib/voice-state-machine';
import { useMongars } from '@/providers/mongars-provider';
import type { ChatCitation, ChatRequest, JsonValue } from '@/types/mongars-api';

const suggestions = ['Summarize my day', 'Search project memory', 'Show active tasks'];

type ChatSource = { label: string; url?: string };
type ChatDisplayMessage = {
  id: string;
  role: 'assistant' | 'user';
  text: string;
  timestamp: string;
  sources?: ChatSource[];
};

const webSearchModes = ['off', 'auto', 'required'] as const;
type WebSearchMode = NonNullable<ChatRequest['web_search']>;

const VOICE_LIMITS = {
  maxUtteranceSeconds: 30,
  maxUploadBytes: 1_000_000,
  sttProvider: 'local adapter (not yet implemented)',
  sttModelDigest: 'pending',
};

const webSearchModeLabels: Record<WebSearchMode, string> = {
  off: 'Off',
  auto: 'Auto',
  required: 'Required',
};

function voiceReducer(state: VoiceLoopState, event: VoiceLoopEvent): VoiceLoopState {
  return nextVoiceState(state, event);
}

function normalizeWebSource(source: unknown): ChatSource | null {
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

function normalizeCitation(citation: ChatCitation): ChatSource {
  const locator = locatorLabel(citation.locator);
  const title = citation.title?.trim() || citation.kind;
  const label = `${citation.key} · ${title}${locator ? ` · ${locator}` : ''}`;
  if (!citation.url) return { label };
  try {
    const parsed = new URL(citation.url);
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) return { label };
    return { label, url: parsed.toString() };
  } catch {
    return { label };
  }
}

function locatorLabel(locator: { [key: string]: JsonValue } | null): string | null {
  if (!locator) return null;
  const page = locator.page_number ?? locator.page;
  if (typeof page === 'number' && Number.isFinite(page)) return `page ${page}`;
  const headings = locator.heading_path;
  if (Array.isArray(headings)) {
    const text = headings.filter((item): item is string => typeof item === 'string').join(' › ');
    if (text) return text;
  }
  const lineStart = locator.line_start;
  const lineEnd = locator.line_end;
  if (typeof lineStart === 'number') {
    return typeof lineEnd === 'number' && lineEnd !== lineStart
      ? `lines ${lineStart}–${lineEnd}`
      : `line ${lineStart}`;
  }
  return null;
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
  const { hasToken } = useMongars();
  const chat = useStreamingChat();
  const [draft, setDraft] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatDisplayMessage[]>([]);
  const [lastModel, setLastModel] = useState<string | null>(null);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>('auto');
  const [voiceState, dispatchVoiceEvent] = useReducer(voiceReducer, 'idle');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [continuousVoiceLoop, setContinuousVoiceLoop] = useState(false);

  function dispatchVoiceAction(event: VoiceLoopEvent): void {
    if (!canTransition(voiceState, event)) {
      setVoiceError(`Cannot transition ${voiceState} with ${event}`);
      return;
    }
    setVoiceError(null);
    dispatchVoiceEvent(event);
  }

  const voiceVisual: string =
    voiceState === 'listening'
      ? '▁ ▂ ▂ ▄ ▅ █ █ ▃ ▁'
      : voiceState === 'finalizing'
        ? '⏺ finalizing transcription'
        : voiceState === 'thinking'
          ? '… awaiting model response'
          : voiceState === 'speaking'
            ? '♪ ♪ ♫ ♪'
            : '—';

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
    const handle = setTimeout(() => {
      dispatchVoiceAction('auto_restart');
    }, 0);
    return () => {
      clearTimeout(handle);
    };
  }, [continuousVoiceLoop, voiceState]);

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
      const citationSources = Array.isArray(response.citations)
        ? response.citations.map(normalizeCitation)
        : [];
      const fallbackSources = Array.isArray(response.sources)
        ? response.sources.map(normalizeWebSource).filter((source) => source !== null)
        : [];
      const responseSources = citationSources.length
        ? citationSources
        : [
            ...fallbackSources,
            ...(response.memory_hits ? [{ label: `${response.memory_hits} memory hits` }] : []),
          ];
      setSessionId(response.session_id);
      setLastModel(response.model);
      setMessages((current) => [
        ...current,
        {
          id: response.trace_id,
          role: 'assistant',
          text: response.answer,
          timestamp: 'Now',
          sources: responseSources.length ? responseSources : undefined,
        },
      ]);
      setDraft((current) => (current.trim() === text ? '' : current));
      chat.reset();
    } catch {
      // The accepted user turn remains visible; the hook exposes a bounded error below.
    }
  }

  const displayMessages: ChatDisplayMessage[] = chat.isPending
    ? [
        ...messages,
        {
          id: 'assistant-streaming',
          role: 'assistant',
          text: chat.partialText || '…',
          timestamp: chat.attempt > 1 ? `Retry ${chat.attempt}` : 'Streaming',
        },
      ]
    : messages;

  return (
    <ScreenScroll>
      <SurfaceCard tone="primary">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <BrandMark compact />
          <View style={{ flex: 1, gap: 3 }}>
            <Text selectable style={{ color: theme.text, fontSize: 16, fontWeight: '700' }}>
              Local Cortex
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 13 }}>
              {lastModel ?? 'Local model'} · private station
            </Text>
          </View>
          <StatusPill
            label={
              chat.isPending
                ? `Streaming ${chat.attempt || 1}`
                : hasToken
                  ? 'Connected'
                  : 'Token needed'
            }
            tone={chat.isPending ? 'primary' : hasToken ? 'positive' : 'warning'}
          />
        </View>
        <View
          style={{
            borderTopWidth: 1,
            borderColor: theme.border,
            marginTop: 12,
            paddingTop: 12,
            gap: 6,
          }}
        >
          <Text selectable style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
            VOICE LOOP (SAFE FOUNDATION)
          </Text>
          <Text selectable style={{ color: theme.text, fontSize: 12 }}>
            State: {nextLabel(voiceState)}
          </Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              accessibilityRole="button"
              onPress={() => dispatchVoiceAction(primaryVoiceEvent)}
              style={({ pressed }) => ({
                backgroundColor: theme.surface,
                borderColor: theme.border,
                borderRadius: 999,
                borderWidth: 1,
                paddingHorizontal: 10,
                paddingVertical: 7,
                opacity: pressed ? 0.75 : 1,
              })}
            >
              <Text selectable style={{ color: theme.text, fontSize: 11, fontWeight: '700' }}>
                {voiceState === 'idle'
                  ? 'Request permission'
                  : voiceState === 'requesting_permission'
                    ? 'Grant permission'
                    : voiceState === 'listening'
                      ? 'Stop recording'
                      : voiceState === 'finalizing'
                        ? 'Finalize transcript'
                        : voiceState === 'thinking'
                          ? 'Start speaking'
                          : voiceState === 'speaking'
                            ? 'Stop TTS'
                            : 'Cancel'}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              onPress={() => setContinuousVoiceLoop((enabled) => !enabled)}
              style={({ pressed }) => ({
                backgroundColor: theme.surface,
                borderColor: theme.border,
                borderRadius: 999,
                borderWidth: 1,
                paddingHorizontal: 10,
                paddingVertical: 7,
                opacity: pressed ? 0.75 : 1,
              })}
            >
              <Text selectable style={{ color: theme.text, fontSize: 11, fontWeight: '700' }}>
                Continuous loop: {continuousVoiceLoop ? 'On' : 'Off'}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              onPress={() => dispatchVoiceAction('cancel')}
              style={({ pressed }) => ({
                backgroundColor: theme.surfaceMuted,
                borderColor: theme.border,
                borderRadius: 999,
                borderWidth: 1,
                paddingHorizontal: 10,
                paddingVertical: 7,
                opacity: pressed ? 0.75 : 1,
              })}
            >
              <Text
                selectable
                style={{ color: theme.textTertiary, fontSize: 11, fontWeight: '700' }}
              >
                Cancel
              </Text>
            </Pressable>
          </View>
          <Text
            selectable
            accessibilityRole="text"
            style={{ color: theme.textTertiary, fontSize: 11 }}
          >
            Waveform fallback: {voiceVisual}
          </Text>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
            Audio retention: no raw audio is persisted by default; explicit export is required.
          </Text>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
            STT identity: {VOICE_LIMITS.sttProvider} · digest: {VOICE_LIMITS.sttModelDigest}
          </Text>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
            Request limits: {VOICE_LIMITS.maxUtteranceSeconds}s max utterance,{' '}
            {VOICE_LIMITS.maxUploadBytes} max upload bytes
          </Text>
          {voiceError ? (
            <Text style={{ color: theme.warning, fontSize: 11 }}>{voiceError}</Text>
          ) : null}
        </View>
      </SurfaceCard>

      <View style={{ gap: 12 }}>
        {!displayMessages.length ? (
          <SurfaceCard title="Private, local conversation">
            <Text selectable style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 20 }}>
              Ask Cortex to reason over your indexed memory or coordinate a local task. Nothing is
              sent until you press Send.
            </Text>
          </SurfaceCard>
        ) : null}
        {displayMessages.map((message) => {
          const isUser = message.role === 'user';
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
                  backgroundColor: isUser ? theme.primary : theme.surface,
                  borderColor: isUser ? theme.primary : theme.border,
                  borderCurve: 'continuous',
                  borderRadius: radii.large,
                  borderBottomRightRadius: isUser ? 8 : radii.large,
                  borderBottomLeftRadius: isUser ? radii.large : 8,
                  borderWidth: 1,
                  paddingHorizontal: 15,
                  paddingVertical: 12,
                  gap: 10,
                  boxShadow: '0 5px 16px rgba(27, 20, 49, 0.05)',
                }}
              >
                <Text
                  selectable
                  style={{
                    color: isUser ? theme.primaryContrast : theme.text,
                    fontSize: 15,
                    lineHeight: 21,
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
                                  'Could not open source',
                                  'The source link could not be opened on this device.',
                                );
                              })
                            : undefined
                        }
                        style={({ pressed }) => ({
                          backgroundColor: theme.surfaceMuted,
                          borderRadius: 999,
                          maxWidth: 260,
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
            </View>
          );
        })}
      </View>

      <View style={{ gap: 8 }}>
        <Text selectable style={{ color: theme.textSecondary, fontSize: 12, fontWeight: '600' }}>
          QUICK START
        </Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {suggestions.map((suggestion) => (
            <Pressable
              accessibilityRole="button"
              key={suggestion}
              onPress={() => setDraft(suggestion)}
              style={({ pressed }) => ({
                backgroundColor: pressed ? theme.primarySoft : theme.surface,
                borderColor: pressed ? theme.primary : theme.border,
                borderRadius: 999,
                borderWidth: 1,
                paddingHorizontal: 12,
                paddingVertical: 8,
              })}
            >
              <Text style={{ color: theme.text, fontSize: 12, fontWeight: '600' }}>
                {suggestion}
              </Text>
            </Pressable>
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
          padding: 10,
          gap: 8,
          boxShadow: '0 8px 24px rgba(27, 20, 49, 0.08)',
        }}
      >
        <View style={{ gap: 6 }}>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
            WEB SEARCH
          </Text>
          <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', gap: 6 }}>
            {webSearchModes.map((mode) => {
              const selected = webSearchMode === mode;
              return (
                <Pressable
                  accessibilityLabel={`${webSearchModeLabels[mode]} web search`}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: selected }}
                  key={mode}
                  onPress={() => setWebSearchMode(mode)}
                  style={({ pressed }) => ({
                    alignItems: 'center',
                    backgroundColor: selected ? theme.primary : theme.surfaceMuted,
                    borderColor: selected ? theme.primary : theme.border,
                    borderRadius: 999,
                    borderWidth: 1,
                    flex: 1,
                    opacity: pressed ? 0.75 : 1,
                    paddingHorizontal: 8,
                    paddingVertical: 7,
                  })}
                >
                  <Text
                    style={{
                      color: selected ? theme.primaryContrast : theme.textSecondary,
                      fontSize: 11,
                      fontWeight: '700',
                    }}
                  >
                    {webSearchModeLabels[mode]}
                  </Text>
                </Pressable>
              );
            })}
          </View>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 10, lineHeight: 14 }}>
            {webSearchMode === 'off'
              ? 'Never send a web query.'
              : webSearchMode === 'required'
                ? 'Search the web for this message.'
                : 'Search only when your message explicitly asks for it.'}
          </Text>
        </View>
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
            fontSize: 16,
            lineHeight: 22,
            minHeight: 54,
            maxHeight: 144,
            paddingHorizontal: 7,
            paddingVertical: 7,
            textAlignVertical: 'top',
          }}
          value={draft}
        />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <Text selectable style={{ color: theme.textTertiary, flex: 1, fontSize: 11 }}>
            Local inference · web search {webSearchMode}
          </Text>
          {chat.isPending ? (
            <Pressable
              accessibilityRole="button"
              onPress={chat.cancel}
              style={({ pressed }) => ({
                alignItems: 'center',
                backgroundColor: theme.surfaceMuted,
                borderRadius: 999,
                height: 38,
                justifyContent: 'center',
                opacity: pressed ? 0.75 : 1,
                paddingHorizontal: 14,
              })}
            >
              <Text style={{ color: theme.textSecondary, fontSize: 13, fontWeight: '700' }}>
                Cancel
              </Text>
            </Pressable>
          ) : null}
          <Pressable
            accessibilityRole="button"
            disabled={!draft.trim() || chat.isPending}
            onPress={() => void submitMessage()}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor:
                draft.trim() && !chat.isPending ? theme.primary : theme.surfaceMuted,
              borderRadius: 999,
              height: 38,
              justifyContent: 'center',
              opacity: pressed ? 0.75 : 1,
              paddingHorizontal: 17,
            })}
          >
            <Text
              style={{
                color:
                  draft.trim() && !chat.isPending ? theme.primaryContrast : theme.textTertiary,
                fontSize: 13,
                fontWeight: '700',
              }}
            >
              {chat.isPending ? 'Streaming…' : 'Send'}
            </Text>
          </Pressable>
        </View>
      </View>
      {chat.error ? (
        <SurfaceCard tone="danger" title="Response failed">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {chat.error.message}
          </Text>
        </SurfaceCard>
      ) : null}
    </ScreenScroll>
  );
}
