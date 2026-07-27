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
        borderColor: theme.border,
        borderWidth: 1,
        gap: 10,
        padding: 13,
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
                  fontSize: 10,
                  fontWeight: '700',
                  textTransform: 'uppercase',
                }}
              >
                {eyebrow}
              </Text>
            ) : null}
            {title ? (
              <Text selectable style={{ color: theme.text, fontSize: 15, fontWeight: '700' }}>
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
