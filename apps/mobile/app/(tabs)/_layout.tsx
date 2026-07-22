import { Tabs } from 'expo-router';

import { HapticTab } from '@/components/haptic-tab';
import { TabGlyph } from '@/components/tab-glyph';
import { useAppTheme } from '@/hooks/use-app-theme';

export default function TabLayout() {
  const theme = useAppTheme();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: theme.background },
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textTertiary,
        tabBarButton: HapticTab,
        tabBarHideOnKeyboard: true,
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600' },
        tabBarStyle: {
          backgroundColor: theme.tabBar,
          borderTopColor: theme.border,
        },
      }}
    >
      <Tabs.Screen
        name="(chat)"
        options={{
          title: 'Chat',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="chat" />
          ),
        }}
      />
      <Tabs.Screen
        name="(memory)"
        options={{
          title: 'Memory',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="memory" />
          ),
        }}
      />
      <Tabs.Screen
        name="(tasks)"
        options={{
          title: 'Tasks',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="tasks" />
          ),
        }}
      />
      <Tabs.Screen
        name="(settings)"
        options={{
          title: 'Settings',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph color={color} focused={focused} glyph="settings" />
          ),
        }}
      />
    </Tabs>
  );
}
