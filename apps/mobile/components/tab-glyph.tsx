import { Text } from 'react-native';

type TabGlyphProps = {
  color: string;
  glyph: 'chat' | 'memory' | 'settings' | 'tasks';
  focused: boolean;
};

const glyphs = {
  chat: '●',
  memory: '◆',
  tasks: '✓',
  settings: '⚙',
} as const;

export function TabGlyph({ color, focused, glyph }: TabGlyphProps) {
  return (
    <Text
      accessibilityElementsHidden
      style={{
        color,
        fontSize: glyph === 'settings' ? 21 : 19,
        fontWeight: focused ? '800' : '500',
        opacity: focused ? 1 : 0.82,
      }}
    >
      {glyphs[glyph]}
    </Text>
  );
}
