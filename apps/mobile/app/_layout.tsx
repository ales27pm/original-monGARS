import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useMemo } from 'react';

import { useAppTheme } from '@/hooks/use-app-theme';
import { useColorScheme } from '@/hooks/use-color-scheme';
import { MongarsProvider } from '@/providers/mongars-provider';

export default function RootLayout() {
  const colorScheme = useColorScheme();
  const appTheme = useAppTheme();
  const navigationTheme = useMemo(() => {
    const baseTheme = colorScheme === 'dark' ? DarkTheme : DefaultTheme;
    return {
      ...baseTheme,
      colors: {
        ...baseTheme.colors,
        background: appTheme.background,
        border: appTheme.border,
        card: appTheme.surface,
        primary: appTheme.primary,
        text: appTheme.text,
      },
    };
  }, [appTheme, colorScheme]);

  return (
    <MongarsProvider>
      <ThemeProvider value={navigationTheme}>
        <Stack screenOptions={{ contentStyle: { backgroundColor: appTheme.background } }}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        </Stack>
        <StatusBar style={colorScheme === 'dark' ? 'light' : 'dark'} />
      </ThemeProvider>
    </MongarsProvider>
  );
}
