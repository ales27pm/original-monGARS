import type { ColorValue } from 'react-native';

import { AppIcon, type AppIconName } from '@/components/app-icon';

type TabGlyphProps = {
  color: ColorValue;
  glyph: 'chat' | 'memory' | 'settings' | 'tasks';
  focused: boolean;
};

const glyphs: Record<TabGlyphProps['glyph'], AppIconName> = {
  chat: 'chat',
  memory: 'memory',
  settings: 'settings',
  tasks: 'tasks',
};

export function TabGlyph({ color, focused, glyph }: TabGlyphProps) {
  return <AppIcon color={color} name={glyphs[glyph]} size={focused ? 21 : 20} />;
}
