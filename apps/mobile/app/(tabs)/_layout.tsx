import { Tabs } from 'expo-router';

import { TabGlyph } from '@/components/tab-glyph';
import { useAppTheme } from '@/hooks/use-app-theme';

export default function TabLayout() {
  const theme = useAppTheme();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: theme.background },
        tabBarActiveTintColor: theme.primary as string,
        tabBarInactiveTintColor: theme.textTertiary as string,
        tabBarHideOnKeyboard: true,
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600' },
        tabBarStyle: {
          backgroundColor: theme.tabBar,
          borderTopColor: theme.border,
          minHeight: 64,
          paddingBottom: 6,
          paddingTop: 5,
        },
      }}
    >
      <Tabs.Screen
        name="(chat)"
        options={{
          href: '/(tabs)/(chat)',
          title: 'Chat',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="chat" />
          ),
        }}
      />
      <Tabs.Screen
        name="(memory)"
        options={{
          href: '/(tabs)/(memory)',
          title: 'Memory',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="memory" />
          ),
        }}
      />
      <Tabs.Screen
        name="(tasks)"
        options={{
          href: '/(tabs)/(tasks)',
          title: 'Tasks',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="tasks" />
          ),
        }}
      />
      <Tabs.Screen
        name="(settings)"
        options={{
          href: '/(tabs)/(settings)',
          title: 'Settings',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="settings" />
          ),
        }}
      />
    </Tabs>
  );
}
