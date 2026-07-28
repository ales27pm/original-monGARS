import * as Haptics from 'expo-haptics';
import { useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Alert,
  KeyboardAvoidingView,
  Linking,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  Pressable,
  ScrollView,
  Text,
  View,
} from 'react-native';

import {
  ChatComposer,
  type ChatWebSearchMode,
} from '@/components/chat-composer';
import {
  ChatEmptyState,
  type ChatSuggestion,
} from '@/components/chat-empty-state';
import { ChatFeedbackControls } from '@/components/chat-feedback-controls';
import { ScreenScroll } from '@/components/screen-scroll';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { SystemIcon } from '@/components/system-icon';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useStreamingChat } from '@/hooks/use-mongars-api';
import { useMongars } from '@/providers/mongars-provider';
import type {
  ChatCitation,
  ChatResponse,
  JsonValue,
} from '@/types/mongars-api';

const suggestions: readonly ChatSuggestion[] = [
  {
    detail: 'Turn recent memory into a concise brief',
    icon: 'sparkles',
    title: 'Summarize my day',
  },
  {
    detail: 'Find decisions, notes, and prior context',
    icon: 'memory-fill',
    title: 'Search project memory',
  },
  {
    detail: 'Review queued and running work',
    icon: 'tasks-fill',
    title: 'Show active tasks',
  },
];

type DisplaySource = { label: string; url?: string };
type ChatDisplayMessage = {
  id: string;
  role: 'assistant' | 'user';
  text: string;
  sources?: DisplaySource[];
};

type WebSearchMode = ChatWebSearchMode;

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
  const { hasToken } = useMongars();
  const chat = useStreamingChat();
  const [draft, setDraft] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatDisplayMessage[]>([]);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>('auto');
  const scrollRef = useRef<ScrollView>(null);
  const shouldFollowConversation = useRef(true);
  const wasPending = useRef(false);

  useEffect(() => {
    if (process.env.EXPO_OS !== 'ios' || wasPending.current === chat.isPending) return;
    void AccessibilityInfo.announceForAccessibility(
      chat.isPending ? 'Cortex is responding' : 'Response complete',
    );
    wasPending.current = chat.isPending;
  }, [chat.isPending]);

  async function submitMessage() {
    const text = draft.trim();
    if (!text || chat.isPending) return;
    shouldFollowConversation.current = true;
    if (process.env.EXPO_OS === 'ios') {
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    }
    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: 'user', text },
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
      }
    : null;
  const displayedMessages = transientMessage ? [...messages, transientMessage] : messages;

  function handleConversationScroll(event: NativeSyntheticEvent<NativeScrollEvent>): void {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const distanceFromBottom = contentSize.height - contentOffset.y - layoutMeasurement.height;
    shouldFollowConversation.current = distanceFromBottom < 80;
  }

  function selectSuggestion(suggestion: string): void {
    setDraft(suggestion);
    if (process.env.EXPO_OS === 'ios') {
      void Haptics.selectionAsync();
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={process.env.EXPO_OS === 'ios' ? 'padding' : undefined}
      style={{ backgroundColor: theme.background, flex: 1 }}
    >
      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          gap: 18,
          paddingHorizontal: 16,
          paddingTop: 14,
          paddingBottom: 22,
        }}
        contentInsetAdjustmentBehavior="automatic"
        keyboardDismissMode="interactive"
        keyboardShouldPersistTaps="handled"
        onContentSizeChange={() => {
          if (shouldFollowConversation.current && displayedMessages.length) {
            scrollRef.current?.scrollToEnd({ animated: !chat.isPending });
          }
        }}
        onScroll={handleConversationScroll}
        ref={scrollRef}
        scrollEventThrottle={16}
        showsVerticalScrollIndicator={false}
        style={{ backgroundColor: theme.background, flex: 1 }}
      >
        {chat.isPending || !hasToken ? (
          <View accessibilityLiveRegion="polite">
            <StatusPill
              label={chat.isPending ? 'Streaming' : hasToken ? 'Connected' : 'Token needed'}
              tone={chat.isPending ? 'primary' : hasToken ? 'positive' : 'warning'}
            />
          </View>
        ) : null}

        {!displayedMessages.length ? (
          <ChatEmptyState onSelect={selectSuggestion} suggestions={suggestions} />
        ) : (
          <View style={{ gap: 22 }}>
            {displayedMessages.map((message) => {
              const isUser = message.role === 'user';
              const isCommittedAssistant = !isUser && message.id !== 'assistant-streaming';

              if (isUser) {
                return (
                  <View key={message.id} style={{ alignItems: 'flex-end', paddingLeft: 54 }}>
                    <View
                      style={{
                        backgroundColor: theme.primary,
                        borderCurve: 'continuous',
                        borderRadius: radii.large,
                        borderBottomRightRadius: 7,
                        maxWidth: '92%',
                        paddingHorizontal: 15,
                        paddingVertical: 11,
                      }}
                    >
                      <Text
                        selectable
                        style={{
                          color: theme.primaryContrast,
                          fontSize: 15,
                          lineHeight: 21,
                        }}
                      >
                        {message.text}
                      </Text>
                    </View>
                  </View>
                );
              }

              return (
                <View
                  key={message.id}
                  style={{ alignItems: 'flex-start', flexDirection: 'row', gap: 10 }}
                >
                  <View
                    style={{
                      alignItems: 'center',
                      backgroundColor: theme.primarySoft,
                      borderRadius: 10,
                      height: 30,
                      justifyContent: 'center',
                      marginTop: 1,
                      width: 30,
                    }}
                  >
                    <SystemIcon color={theme.primary} name="sparkles" size={14} />
                  </View>
                  <View style={{ flex: 1, gap: 10, paddingTop: 4 }}>
                    <Text
                      selectable
                      style={{ color: theme.text, fontSize: 15, lineHeight: 22 }}
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
                              justifyContent: 'center',
                              minHeight: 44,
                              maxWidth: 280,
                              opacity: pressed ? 0.7 : 1,
                              paddingHorizontal: 9,
                              paddingVertical: 5,
                            })}
                          >
                            <Text
                              ellipsizeMode="tail"
                              numberOfLines={1}
                              selectable
                              style={{
                                color: theme.textSecondary,
                                fontSize: 11,
                                fontWeight: '600',
                              }}
                            >
                              {source.label}
                            </Text>
                          </Pressable>
                        ))}
                      </View>
                    ) : null}
                    {isCommittedAssistant ? (
                      <ChatFeedbackControls traceId={message.id} />
                    ) : null}
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </ScrollView>

      {chat.error ? (
        <View accessibilityRole="alert" style={{ paddingHorizontal: 12, paddingTop: 8 }}>
          <SurfaceCard tone="danger" title="Response interrupted">
            <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
              {chat.error.message}
            </Text>
          </SurfaceCard>
        </View>
      ) : null}

      <ChatComposer
        draft={draft}
        isPending={chat.isPending}
        onCancel={chat.cancel}
        onChangeDraft={setDraft}
        onSubmit={() => void submitMessage()}
        onWebSearchModeChange={setWebSearchMode}
        webSearchMode={webSearchMode}
      />
    </KeyboardAvoidingView>
  );
}
