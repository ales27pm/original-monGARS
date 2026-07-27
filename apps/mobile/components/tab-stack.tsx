import { Stack } from 'expo-router';

import {
  AppHeaderTitle,
  HeaderMenuButton,
  HeaderShieldButton,
} from '@/components/app-header';
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
        headerLargeTitle: false,
        headerShadowVisible: false,
        headerStyle: { backgroundColor: theme.surface },
        headerTitle: AppHeaderTitle,
        headerTitleAlign: 'center',
        headerTransparent: false,
        headerRight: HeaderShieldButton,
      }}
    >
      <Stack.Screen name="index" options={{ headerLeft: HeaderMenuButton, title }} />
    </Stack>
  );
}
