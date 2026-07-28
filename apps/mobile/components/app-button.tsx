import { ActivityIndicator, Pressable, Text, type GestureResponderEvent, type StyleProp, type ViewStyle } from 'react-native';

import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';

type ButtonTone = 'danger' | 'neutral' | 'primary' | 'warning';
type ButtonVariant = 'outline' | 'soft' | 'solid';
type ButtonSize = 'compact' | 'regular' | 'large';

type AppButtonProps = {
  accessibilityLabel?: string;
  disabled?: boolean;
  fullWidth?: boolean;
  label: string;
  loading?: boolean;
  onPress: (event: GestureResponderEvent) => void;
  size?: ButtonSize;
  style?: StyleProp<ViewStyle>;
  tone?: ButtonTone;
  variant?: ButtonVariant;
};

const sizeStyles = {
  compact: { fontSize: 11, minHeight: 30, paddingHorizontal: 11, paddingVertical: 6 },
  regular: { fontSize: 13, minHeight: 38, paddingHorizontal: 14, paddingVertical: 9 },
  large: { fontSize: 14, minHeight: 44, paddingHorizontal: 16, paddingVertical: 11 },
} as const;

export function AppButton({
  accessibilityLabel,
  disabled = false,
  fullWidth = false,
  label,
  loading = false,
  onPress,
  size = 'regular',
  style,
  tone = 'primary',
  variant = 'solid',
}: AppButtonProps) {
  const theme = useAppTheme();
  const blocked = disabled || loading;
  const toneColors = {
    danger: {
      base: theme.danger,
      contrast: theme.primaryContrast,
      soft: theme.dangerSoft,
    },
    neutral: {
      base: theme.textSecondary,
      contrast: theme.surface,
      soft: theme.surfaceMuted,
    },
    primary: {
      base: theme.primary,
      contrast: theme.primaryContrast,
      soft: theme.primarySoft,
    },
    warning: {
      base: theme.warning,
      contrast: theme.primaryContrast,
      soft: theme.warningSoft,
    },
  } as const;
  const colors = toneColors[tone];
  const palette =
    variant === 'solid'
      ? {
          background: blocked ? theme.surfaceMuted : colors.base,
          border: blocked ? theme.surfaceMuted : colors.base,
          foreground: blocked ? theme.textTertiary : colors.contrast,
        }
      : variant === 'outline'
        ? {
            background: theme.surface,
            border: blocked ? theme.border : colors.base,
            foreground: blocked ? theme.textTertiary : colors.base,
          }
        : {
            background: blocked ? theme.surfaceMuted : colors.soft,
            border: blocked ? theme.surfaceMuted : colors.base,
            foreground: blocked ? theme.textTertiary : colors.base,
          };
  const sizing = sizeStyles[size];

  return (
    <Pressable
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityRole="button"
      accessibilityState={{ busy: loading, disabled: blocked }}
      disabled={blocked}
      onPress={onPress}
      style={({ pressed }) => [
        {
          alignItems: 'center',
          alignSelf: fullWidth ? 'stretch' : 'flex-start',
          backgroundColor: palette.background,
          borderColor: palette.border,
          borderCurve: 'continuous',
          borderRadius: radii.medium,
          borderWidth: variant === 'solid' ? 0 : 1,
          flex: fullWidth ? 1 : undefined,
          justifyContent: 'center',
          minHeight: sizing.minHeight,
          opacity: pressed ? 0.74 : 1,
          paddingHorizontal: sizing.paddingHorizontal,
          paddingVertical: sizing.paddingVertical,
        },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={palette.foreground} />
      ) : (
        <Text
          style={{
            color: palette.foreground,
            fontSize: sizing.fontSize,
            fontWeight: '700',
            textAlign: 'center',
          }}
        >
          {label}
        </Text>
      )}
    </Pressable>
  );
}
