import * as Haptics from 'expo-haptics';
import { useState } from 'react';
import { Alert, Linking, Pressable, Text, TextInput, View } from 'react-native';

import { BrandMark } from '@/components/brand-mark';
import { ScreenScroll } from '@/components/screen-scroll';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useChat } from '@/hooks/use-mongars-api';
import { useMongars } from '@/providers/mongars-provider';
import type { ChatRequest } from '@/types/mongars-api';

const suggestions = ['Summarize my day', 'Search project memory', 'Show active tasks'];

type ChatDisplayMessage = {
  id: string;
  role: 'assistant' | 'user';
  text: string;
  timestamp: string;
  sources?: { label: string; url?: string }[];
};

const webSearchModes = ['off', 'auto', 'required'] as const;
type WebSearchMode = NonNullable<ChatRequest['web_search']>;

const webSearchModeLabels: Record<WebSearchMode, string> = {
  off: 'Off',
  auto: 'Auto',
  required: 'Required',
};

function normalizeWebSource(source: unknown): { label: string; url: string } | null {
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
  const chat = useChat();
  const [draft, setDraft] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatDisplayMessage[]>([]);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>('auto');

  async function submitMessage() {
    const text = draft.trim();
    if (!text || chat.isPending) return;
    if (process.env.EXPO_OS === 'ios') {
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    }
    const pendingMessageId = `user-${Date.now()}`;
    setMessages((current) => [
      ...current,
      { id: pendingMessageId, role: 'user', text, timestamp: 'Now' },
    ]);
    try {
      const response = await chat.mutate({
        message: text,
        session_id: sessionId,
        require_local_only: true,
        web_search: webSearchMode,
      });
      const responseSources = Array.isArray(response.sources)
        ? response.sources.map(normalizeWebSource).filter((source) => source !== null)
        : [];
      setSessionId(response.session_id);
      setMessages((current) => [
        ...current,
        {
          id: response.trace_id,
          role: 'assistant',
          text: response.answer,
          timestamp: 'Now',
          sources:
            responseSources.length || response.memory_hits
              ? [
                  ...responseSources,
                  ...(response.memory_hits
                    ? [{ label: `${response.memory_hits} memory hits` }]
                    : []),
                ]
              : undefined,
        },
      ]);
      setDraft((current) => (current.trim() === text ? '' : current));
    } catch {
      setMessages((current) => current.filter((message) => message.id !== pendingMessageId));
      // The mutation exposes a user-readable error below the composer.
    }
  }

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
              qwen3:4b-instruct · RTX 2070
            </Text>
          </View>
          <StatusPill
            label={chat.isPending ? 'Thinking' : hasToken ? 'Connected' : 'Token needed'}
            tone={chat.isPending ? 'primary' : hasToken ? 'positive' : 'warning'}
          />
        </View>
      </SurfaceCard>

      <View style={{ gap: 12 }}>
        {!messages.length ? (
          <SurfaceCard title="Private, local conversation">
            <Text selectable style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 20 }}>
              Ask Cortex to reason over your indexed memory or coordinate a local task. Nothing is
              sent until you press Send.
            </Text>
          </SurfaceCard>
        ) : null}
        {messages.map((message) => {
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
                                  'Could not open web result',
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
              {chat.isPending ? 'Thinking…' : 'Send'}
            </Text>
          </Pressable>
        </View>
      </View>
      {chat.error ? (
        <SurfaceCard tone="danger" title="Message not sent">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {chat.error.message}
          </Text>
        </SurfaceCard>
      ) : null}
    </ScreenScroll>
  );
}
