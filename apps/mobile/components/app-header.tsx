import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Modal, Pressable, Text, View } from 'react-native';

import { AppIcon, type AppIconName } from '@/components/app-icon';
import { useAppTheme } from '@/hooks/use-app-theme';

const destinations: readonly {
  href: '/(tabs)/(chat)' | '/(tabs)/(memory)' | '/(tabs)/(tasks)' | '/(tabs)/(settings)';
  icon: AppIconName;
  label: string;
}[] = [
  { href: '/(tabs)/(chat)', icon: 'chat', label: 'Chat' },
  { href: '/(tabs)/(memory)', icon: 'memory', label: 'Memory' },
  { href: '/(tabs)/(tasks)', icon: 'tasks', label: 'Tasks' },
  { href: '/(tabs)/(settings)', icon: 'settings', label: 'Settings' },
];

export function AppHeaderTitle() {
  const theme = useAppTheme();
  return (
    <Text style={{ color: theme.text, fontSize: 15, fontWeight: '800' }}>
      mon<Text style={{ color: theme.primary }}>GARS</Text>
    </Text>
  );
}

export function HeaderMenuButton() {
  const router = useRouter();
  const theme = useAppTheme();
  const [open, setOpen] = useState(false);

  return (
    <>
      <Pressable
        accessibilityLabel="Open navigation"
        accessibilityRole="button"
        hitSlop={10}
        onPress={() => setOpen(true)}
        style={({ pressed }) => ({ opacity: pressed ? 0.55 : 1, padding: 4 })}
      >
        <AppIcon color={theme.textSecondary} name="menu" size={21} />
      </Pressable>
      <Modal
        animationType="fade"
        onRequestClose={() => setOpen(false)}
        transparent
        visible={open}
      >
        <View style={{ flex: 1, flexDirection: 'row' }}>
          <View
            style={{
              backgroundColor: theme.surface,
              borderRightColor: theme.border,
              borderRightWidth: 1,
              gap: 8,
              paddingHorizontal: 16,
              paddingTop: 54,
              width: 270,
            }}
          >
            <View style={{ marginBottom: 14 }}>
              <AppHeaderTitle />
              <Text style={{ color: theme.textTertiary, fontSize: 11, marginTop: 4 }}>
                Private local intelligence
              </Text>
            </View>
            {destinations.map((destination) => (
              <Pressable
                accessibilityRole="button"
                key={destination.href}
                onPress={() => {
                  setOpen(false);
                  router.navigate(destination.href);
                }}
                style={({ pressed }) => ({
                  alignItems: 'center',
                  backgroundColor: pressed ? theme.primarySoft : 'transparent',
                  borderRadius: 8,
                  flexDirection: 'row',
                  gap: 12,
                  paddingHorizontal: 10,
                  paddingVertical: 11,
                })}
              >
                <AppIcon color={theme.textSecondary} name={destination.icon} size={20} />
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: '600' }}>
                  {destination.label}
                </Text>
              </Pressable>
            ))}
          </View>
          <Pressable
            accessibilityLabel="Close navigation"
            accessibilityRole="button"
            onPress={() => setOpen(false)}
            style={{ backgroundColor: 'rgba(23, 21, 28, 0.24)', flex: 1 }}
          />
        </View>
      </Modal>
    </>
  );
}

export function HeaderShieldButton() {
  const router = useRouter();
  const theme = useAppTheme();
  return (
    <Pressable
      accessibilityLabel="Open security settings"
      accessibilityRole="button"
      hitSlop={10}
      onPress={() => router.navigate('/(tabs)/(settings)')}
      style={({ pressed }) => ({ opacity: pressed ? 0.55 : 1, padding: 4 })}
    >
      <AppIcon color={theme.textSecondary} name="shield" size={21} />
    </Pressable>
  );
}
