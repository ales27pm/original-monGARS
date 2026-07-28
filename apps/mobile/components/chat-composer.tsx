import { useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';

import { SystemIcon } from '@/components/system-icon';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';

export type ChatWebSearchMode = 'auto' | 'off' | 'required';

type ChatComposerProps = {
  draft: string;
  isPending: boolean;
  onCancel: () => void;
  onChangeDraft: (value: string) => void;
  onSubmit: () => void;
  onWebSearchModeChange: (mode: ChatWebSearchMode) => void;
  webSearchMode: ChatWebSearchMode;
};

const webSearchModes = ['off', 'auto', 'required'] as const;
const webSearchModeLabels: Record<ChatWebSearchMode, string> = {
  off: 'Off',
  auto: 'Auto',
  required: 'Required',
};

function webSearchDescription(mode: ChatWebSearchMode): string {
  if (mode === 'off') return 'Web access stays off for this message.';
  if (mode === 'required') return 'Cortex must search before answering.';
  return 'Cortex searches only when your message explicitly asks for web information.';
}

export function ChatComposer({
  draft,
  isPending,
  onCancel,
  onChangeDraft,
  onSubmit,
  onWebSearchModeChange,
  webSearchMode,
}: ChatComposerProps) {
  const theme = useAppTheme();
  const [searchExpanded, setSearchExpanded] = useState(false);
  const canSend = draft.trim().length > 0 && !isPending;

  return (
    <View
      style={{
        backgroundColor: theme.background,
        borderTopColor: theme.border,
        borderTopWidth: 1,
        paddingHorizontal: 12,
        paddingTop: 9,
        paddingBottom: 10,
      }}
    >
      <View
        style={{
          backgroundColor: theme.surface,
          borderColor: theme.border,
          borderCurve: 'continuous',
          borderRadius: radii.large,
          borderWidth: 1,
          overflow: 'hidden',
          boxShadow: '0 10px 30px rgba(19, 16, 38, 0.16)',
        }}
      >
        {searchExpanded ? (
          <View
            style={{
              borderBottomColor: theme.border,
              borderBottomWidth: 1,
              paddingHorizontal: 11,
              paddingTop: 10,
              paddingBottom: 9,
              gap: 8,
            }}
          >
            <View style={{ alignItems: 'center', flexDirection: 'row', gap: 7 }}>
              <SystemIcon color={theme.primary} name="globe" size={15} />
              <Text selectable style={{ color: theme.text, fontSize: 12, fontWeight: '700' }}>
                Web search
              </Text>
            </View>
            <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', gap: 6 }}>
              {webSearchModes.map((mode) => {
                const selected = webSearchMode === mode;
                return (
                  <Pressable
                    accessibilityLabel={`${webSearchModeLabels[mode]} web search`}
                    accessibilityRole="radio"
                    accessibilityState={{ checked: selected }}
                    key={mode}
                    onPress={() => onWebSearchModeChange(mode)}
                    style={({ pressed }) => ({
                      alignItems: 'center',
                      backgroundColor: selected ? theme.primary : theme.surfaceMuted,
                      borderColor: selected ? theme.primary : theme.border,
                    borderRadius: 999,
                    borderWidth: 1,
                    flex: 1,
                    minHeight: 44,
                    opacity: pressed ? 0.72 : 1,
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
              {webSearchDescription(webSearchMode)}
            </Text>
          </View>
        ) : null}

        <View style={{ alignItems: 'flex-end', flexDirection: 'row', gap: 8, padding: 10 }}>
          <TextInput
            accessibilityLabel="Message Cortex"
            maxLength={32_000}
            multiline
            onChangeText={onChangeDraft}
            placeholder="Message Cortex"
            placeholderTextColor={theme.textTertiary}
            selectionColor={theme.primary}
            style={{
              color: theme.text,
              flex: 1,
              fontSize: 16,
              lineHeight: 22,
              maxHeight: 120,
              minHeight: 40,
              paddingHorizontal: 4,
              paddingVertical: 8,
              textAlignVertical: 'top',
            }}
            value={draft}
          />
          <Pressable
            accessibilityLabel="Send message"
            accessibilityRole="button"
            disabled={!canSend}
            onPress={onSubmit}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor: canSend ? theme.primary : theme.surfaceMuted,
              borderRadius: 999,
              height: 44,
              justifyContent: 'center',
              opacity: pressed ? 0.72 : 1,
              width: 44,
            })}
          >
            <SystemIcon
              color={canSend ? theme.primaryContrast : theme.textTertiary}
              name="arrow-up"
              size={17}
            />
          </Pressable>
        </View>

        <View
          style={{
            alignItems: 'center',
            borderTopColor: theme.border,
            borderTopWidth: 1,
            flexDirection: 'row',
            gap: 6,
            minHeight: 46,
            paddingHorizontal: 9,
            paddingVertical: 4,
          }}
        >
          <Pressable
            accessibilityLabel={`Web search ${webSearchMode}`}
            accessibilityRole="button"
            accessibilityState={{ expanded: searchExpanded }}
            onPress={() => setSearchExpanded((expanded) => !expanded)}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor: searchExpanded ? theme.primarySoft : 'transparent',
              borderRadius: 999,
              flexDirection: 'row',
              gap: 6,
              minHeight: 44,
              opacity: pressed ? 0.6 : 1,
              paddingHorizontal: 10,
              paddingVertical: 8,
            })}
          >
            <SystemIcon
              color={searchExpanded ? theme.primary : theme.textSecondary}
              name="globe"
              size={14}
            />
            <Text
              style={{
                color: searchExpanded ? theme.primary : theme.textSecondary,
                fontSize: 11,
                fontWeight: '700',
              }}
            >
              {webSearchModeLabels[webSearchMode]}
            </Text>
          </Pressable>
          <View style={{ flex: 1 }} />
          {isPending ? (
            <Pressable
              accessibilityLabel="Cancel response"
              accessibilityRole="button"
              onPress={onCancel}
              style={({ pressed }) => ({
                alignItems: 'center',
                flexDirection: 'row',
                gap: 5,
                minHeight: 44,
                opacity: pressed ? 0.55 : 1,
                paddingHorizontal: 7,
                paddingVertical: 5,
              })}
            >
              <SystemIcon color={theme.textSecondary} name="stop" size={12} />
              <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
                Stop
              </Text>
            </Pressable>
          ) : (
            <View style={{ alignItems: 'center', flexDirection: 'row', gap: 5, paddingRight: 5 }}>
              <SystemIcon color={theme.positive} name="lock" size={11} />
              <Text selectable style={{ color: theme.textTertiary, fontSize: 10, fontWeight: '600' }}>
                Local model
              </Text>
            </View>
          )}
        </View>
      </View>
    </View>
  );
}
