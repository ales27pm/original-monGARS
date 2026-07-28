import { Pressable, type GestureResponderEvent } from 'react-native';

import { AppIcon, type AppIconName } from '@/components/app-icon';
import { useAppTheme } from '@/hooks/use-app-theme';

type IconButtonProps = {
  accessibilityLabel: string;
  disabled?: boolean;
  icon: AppIconName;
  onPress: (event: GestureResponderEvent) => void;
  selected?: boolean;
  size?: 'compact' | 'regular';
  tone?: 'neutral' | 'primary' | 'danger';
  variant?: 'plain' | 'soft' | 'solid';
};

export function IconButton({
  accessibilityLabel,
  disabled = false,
  icon,
  onPress,
  selected = false,
  size = 'regular',
  tone = 'neutral',
  variant = 'plain',
}: IconButtonProps) {
  const theme = useAppTheme();
  const dimension = size === 'compact' ? 32 : 38;
  const baseColor =
    tone === 'primary' ? theme.primary : tone === 'danger' ? theme.danger : theme.textSecondary;
  const backgroundColor =
    variant === 'solid'
      ? disabled
        ? theme.surfaceMuted
        : baseColor
      : variant === 'soft' || selected
        ? tone === 'primary'
          ? theme.primarySoft
          : tone === 'danger'
            ? theme.dangerSoft
            : theme.surfaceMuted
        : 'transparent';
  const iconColor =
    variant === 'solid' && !disabled
      ? theme.primaryContrast
      : disabled
        ? theme.textTertiary
        : baseColor;

  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      accessibilityState={{ disabled, selected }}
      disabled={disabled}
      hitSlop={6}
      onPress={onPress}
      style={({ pressed }) => ({
        alignItems: 'center',
        backgroundColor,
        borderColor: variant === 'plain' ? 'transparent' : selected ? baseColor : theme.border,
        borderRadius: 999,
        borderWidth: variant === 'solid' || variant === 'plain' ? 0 : 1,
        height: dimension,
        justifyContent: 'center',
        opacity: pressed ? 0.6 : 1,
        width: dimension,
      })}
    >
      <AppIcon color={iconColor} name={icon} size={size === 'compact' ? 18 : 20} />
    </Pressable>
  );
}
