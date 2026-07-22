import type { PropsWithChildren, ReactNode } from 'react';
import { Text, View } from 'react-native';

import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';

type SurfaceCardProps = PropsWithChildren<{
  eyebrow?: string;
  title?: string;
  trailing?: ReactNode;
  tone?: 'default' | 'primary' | 'positive' | 'warning' | 'danger';
}>;

export function SurfaceCard({
  children,
  eyebrow,
  title,
  trailing,
  tone = 'default',
}: SurfaceCardProps) {
  const theme = useAppTheme();
  const backgrounds = {
    default: theme.surface,
    primary: theme.primarySoft,
    positive: theme.positiveSoft,
    warning: theme.warningSoft,
    danger: theme.dangerSoft,
  } as const;

  return (
    <View
      style={{
        backgroundColor: backgrounds[tone],
        borderRadius: radii.large,
        borderCurve: 'continuous',
        borderWidth: tone === 'default' ? 1 : 0,
        borderColor: theme.border,
        padding: 16,
        gap: 12,
        boxShadow: '0 8px 24px rgba(27, 20, 49, 0.06)',
      }}
    >
      {eyebrow || title || trailing ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ flex: 1, gap: 2 }}>
            {eyebrow ? (
              <Text
                selectable
                style={{
                  color: theme.textSecondary,
                  fontSize: 11,
                  fontWeight: '700',
                  letterSpacing: 0.7,
                  textTransform: 'uppercase',
                }}
              >
                {eyebrow}
              </Text>
            ) : null}
            {title ? (
              <Text selectable style={{ color: theme.text, fontSize: 17, fontWeight: '700' }}>
                {title}
              </Text>
            ) : null}
          </View>
          {trailing}
        </View>
      ) : null}
      {children}
    </View>
  );
}
