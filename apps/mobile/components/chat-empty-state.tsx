import { Pressable, Text, View } from 'react-native';

import { SystemIcon, type SystemIconName } from '@/components/system-icon';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';

export type ChatSuggestion = {
  detail: string;
  icon: SystemIconName;
  title: string;
};

type ChatEmptyStateProps = {
  onSelect: (suggestion: string) => void;
  suggestions: readonly ChatSuggestion[];
};

export function ChatEmptyState({ onSelect, suggestions }: ChatEmptyStateProps) {
  const theme = useAppTheme();

  return (
    <View
      style={{
        alignItems: 'center',
        flexGrow: 1,
        justifyContent: 'center',
        paddingVertical: 24,
        gap: 20,
      }}
    >
      <View
        style={{
          alignItems: 'center',
          backgroundColor: theme.primarySoft,
          borderColor: theme.border,
          borderCurve: 'continuous',
          borderRadius: 24,
          borderWidth: 1,
          height: 64,
          justifyContent: 'center',
          width: 64,
          boxShadow: '0 12px 30px rgba(48, 38, 108, 0.18)',
        }}
      >
        <SystemIcon color={theme.primary} name="sparkles" size={28} />
      </View>

      <View style={{ alignItems: 'center', maxWidth: 360, gap: 8 }}>
        <Text
          selectable
          style={{
            color: theme.text,
            fontSize: 27,
            fontWeight: '800',
            letterSpacing: -0.6,
            textAlign: 'center',
          }}
        >
          What can I help with?
        </Text>
        <Text
          selectable
          style={{
            color: theme.textSecondary,
            fontSize: 14,
            lineHeight: 20,
            textAlign: 'center',
          }}
        >
          Ask Cortex to search your memory, organize work, or reason through a decision.
        </Text>
        <View style={{ alignItems: 'center', flexDirection: 'row', gap: 6, paddingTop: 2 }}>
          <SystemIcon color={theme.positive} name="lock" size={13} />
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11, fontWeight: '600' }}>
            Private and local by default
          </Text>
        </View>
      </View>

      <View style={{ alignSelf: 'stretch', gap: 9 }}>
        {suggestions.map((suggestion) => (
          <Pressable
            accessibilityHint={suggestion.detail}
            accessibilityRole="button"
            key={suggestion.title}
            onPress={() => onSelect(suggestion.title)}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor: pressed ? theme.primarySoft : theme.surface,
              borderColor: pressed ? theme.primary : theme.border,
              borderCurve: 'continuous',
              borderRadius: radii.medium,
              borderWidth: 1,
              flexDirection: 'row',
              gap: 12,
              opacity: pressed ? 0.78 : 1,
              paddingHorizontal: 13,
              paddingVertical: 12,
            })}
          >
            <View
              style={{
                alignItems: 'center',
                backgroundColor: theme.surfaceMuted,
                borderRadius: 12,
                height: 36,
                justifyContent: 'center',
                width: 36,
              }}
            >
              <SystemIcon color={theme.primary} name={suggestion.icon} size={17} />
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <Text selectable style={{ color: theme.text, fontSize: 14, fontWeight: '700' }}>
                {suggestion.title}
              </Text>
              <Text
                selectable
                numberOfLines={1}
                style={{ color: theme.textTertiary, fontSize: 11 }}
              >
                {suggestion.detail}
              </Text>
            </View>
            <SystemIcon color={theme.textTertiary} name="chevron-right" size={14} />
          </Pressable>
        ))}
      </View>
    </View>
  );
}
