import { Text, View } from 'react-native';

import { useAppTheme } from '@/hooks/use-app-theme';

type StatusPillProps = {
  label: string;
  tone?: 'neutral' | 'positive' | 'warning' | 'danger' | 'primary';
};

export function StatusPill({ label, tone = 'neutral' }: StatusPillProps) {
  const theme = useAppTheme();
  const tones = {
    neutral: { background: theme.surfaceMuted, foreground: theme.textSecondary },
    positive: { background: theme.positiveSoft, foreground: theme.positive },
    warning: { background: theme.warningSoft, foreground: theme.warning },
    danger: { background: theme.dangerSoft, foreground: theme.danger },
    primary: { background: theme.primarySoft, foreground: theme.primary },
  } as const;
  const colors = tones[tone];

  return (
    <View
      style={{
        alignSelf: 'flex-start',
        backgroundColor: colors.background,
        borderRadius: 999,
        paddingHorizontal: 10,
        paddingVertical: 5,
      }}
    >
      <Text selectable style={{ color: colors.foreground, fontSize: 11, fontWeight: '700' }}>
        {label}
      </Text>
    </View>
  );
}
