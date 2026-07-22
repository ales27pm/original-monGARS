import { Text, View } from 'react-native';

import { useAppTheme } from '@/hooks/use-app-theme';

type BrandMarkProps = {
  compact?: boolean;
};

export function BrandMark({ compact = false }: BrandMarkProps) {
  const theme = useAppTheme();

  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <View
        style={{
          width: compact ? 32 : 42,
          height: compact ? 32 : 42,
          borderRadius: compact ? 11 : 15,
          borderCurve: 'continuous',
          backgroundColor: theme.primary,
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <View
          style={{
            width: compact ? 14 : 18,
            height: compact ? 14 : 18,
            borderRadius: 999,
            borderWidth: compact ? 3 : 4,
            borderColor: theme.primaryContrast,
          }}
        />
      </View>
      {!compact ? (
        <View>
          <Text selectable style={{ color: theme.text, fontSize: 20, fontWeight: '800' }}>
            monGARS
          </Text>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 12 }}>
            Local personal intelligence
          </Text>
        </View>
      ) : null}
    </View>
  );
}
