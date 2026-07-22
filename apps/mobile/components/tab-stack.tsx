import { Stack } from 'expo-router';

import { useAppTheme } from '@/hooks/use-app-theme';

type TabStackProps = {
  title: string;
};

export function TabStack({ title }: TabStackProps) {
  const theme = useAppTheme();

  return (
    <Stack
      screenOptions={{
        contentStyle: { backgroundColor: theme.background },
        headerBackButtonDisplayMode: 'minimal',
        headerBlurEffect: 'systemChromeMaterial',
        headerLargeTitle: true,
        headerLargeTitleShadowVisible: false,
        headerShadowVisible: false,
        headerStyle: { backgroundColor: theme.background },
        headerTitleStyle: { color: theme.text },
        headerTransparent: true,
      }}
    >
      <Stack.Screen name="index" options={{ title }} />
    </Stack>
  );
}
