import type { ColorValue } from 'react-native';

import { SystemIcon, type SystemIconName } from '@/components/system-icon';

type TabGlyphProps = {
  color: ColorValue;
  glyph: 'chat' | 'memory' | 'settings' | 'tasks';
  focused: boolean;
};

const glyphs: Record<
  TabGlyphProps['glyph'],
  { active: SystemIconName; inactive: SystemIconName }
> = {
  chat: { active: 'message-fill', inactive: 'message' },
  memory: { active: 'memory-fill', inactive: 'memory' },
  tasks: { active: 'tasks-fill', inactive: 'tasks' },
  settings: { active: 'settings-fill', inactive: 'settings' },
} as const;

export function TabGlyph({ color, focused, glyph }: TabGlyphProps) {
  const icon = glyphs[glyph];
  return <SystemIcon color={color} name={focused ? icon.active : icon.inactive} size={21} />;
}
