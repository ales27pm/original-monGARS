import { Pressable, Text, View } from 'react-native';

import { useAppTheme } from '@/hooks/use-app-theme';

export type SegmentedControlOption<T extends string> = {
  accessibilityLabel?: string;
  disabled?: boolean;
  label: string;
  value: T;
};

type SegmentedControlProps<T extends string> = {
  accessibilityLabel: string;
  appearance?: 'capsule' | 'tabs';
  fill?: boolean;
  onChange: (value: T) => void;
  options: readonly SegmentedControlOption<T>[];
  size?: 'compact' | 'regular';
  value: T;
  wrap?: boolean;
};

export function SegmentedControl<T extends string>({
  accessibilityLabel,
  appearance = 'capsule',
  fill = true,
  onChange,
  options,
  size = 'regular',
  value,
  wrap = false,
}: SegmentedControlProps<T>) {
  const theme = useAppTheme();
  const verticalPadding = size === 'compact' ? 7 : 9;
  const horizontalPadding = size === 'compact' ? 9 : 12;
  const fontSize = size === 'compact' ? 11 : 12;

  return (
    <View
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="radiogroup"
      style={{
        borderBottomColor: appearance === 'tabs' ? theme.border : undefined,
        borderBottomWidth: appearance === 'tabs' ? 1 : 0,
        flexDirection: 'row',
        flexWrap: wrap ? 'wrap' : 'nowrap',
        gap: appearance === 'tabs' ? 0 : 7,
      }}
    >
      {options.map((option) => {
        const selected = value === option.value;
        const disabled = option.disabled === true;
        return (
          <Pressable
            accessibilityLabel={option.accessibilityLabel ?? option.label}
            accessibilityRole="radio"
            accessibilityState={{ checked: selected, disabled }}
            disabled={disabled}
            key={option.value}
            onPress={() => onChange(option.value)}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor:
                appearance === 'tabs'
                  ? 'transparent'
                  : selected
                    ? theme.primary
                    : theme.surface,
              borderBottomColor:
                appearance === 'tabs' && selected ? theme.primary : 'transparent',
              borderBottomWidth: appearance === 'tabs' ? 2 : 0,
              borderColor:
                appearance === 'tabs'
                  ? undefined
                  : selected
                    ? theme.primary
                    : theme.border,
              borderRadius: appearance === 'tabs' ? 0 : 999,
              borderWidth: appearance === 'tabs' ? 0 : 1,
              flex: fill ? 1 : undefined,
              minWidth: fill ? 0 : undefined,
              opacity: disabled ? 0.45 : pressed ? 0.74 : 1,
              paddingHorizontal: horizontalPadding,
              paddingVertical: appearance === 'tabs' ? 10 : verticalPadding,
            })}
          >
            <Text
              style={{
                color:
                  appearance === 'tabs'
                    ? selected
                      ? theme.primary
                      : theme.textSecondary
                    : selected
                      ? theme.primaryContrast
                      : theme.textSecondary,
                fontSize,
                fontWeight: '700',
                textAlign: 'center',
              }}
            >
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
