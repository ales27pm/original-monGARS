import { DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useMemo } from 'react';

import { useAppTheme } from '@/hooks/use-app-theme';
import { MongarsProvider } from '@/providers/mongars-provider';

export default function RootLayout() {
  const appTheme = useAppTheme();
  const navigationTheme = useMemo(() => {
    return {
      ...DefaultTheme,
      colors: {
        ...DefaultTheme.colors,
        background: appTheme.background,
        border: appTheme.border,
        card: appTheme.surface,
        primary: appTheme.primary,
        text: appTheme.text,
      },
    };
  }, [appTheme]);

  return (
    <MongarsProvider>
      <ThemeProvider value={navigationTheme}>
        <Stack screenOptions={{ contentStyle: { backgroundColor: appTheme.background } }}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        </Stack>
        <StatusBar style="dark" />
      </ThemeProvider>
    </MongarsProvider>
  );
}
