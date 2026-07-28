import {
  type AndroidSymbol,
  type SFSymbol,
  SymbolView,
} from 'expo-symbols';
import type { ColorValue } from 'react-native';

export type SystemIconName =
  | 'arrow-up'
  | 'chevron-right'
  | 'close'
  | 'globe'
  | 'lock'
  | 'memory'
  | 'memory-fill'
  | 'message'
  | 'message-fill'
  | 'mic'
  | 'settings'
  | 'settings-fill'
  | 'sparkles'
  | 'stop'
  | 'tasks'
  | 'tasks-fill'
  | 'waveform';

type SystemIconProps = {
  color: ColorValue;
  name: SystemIconName;
  size?: number;
};

type CrossPlatformSymbol = {
  android: AndroidSymbol;
  ios: SFSymbol;
  web: AndroidSymbol;
};

const symbolNames: Record<SystemIconName, CrossPlatformSymbol> = {
  'arrow-up': { android: 'arrow_upward', ios: 'arrow.up', web: 'arrow_upward' },
  'chevron-right': { android: 'chevron_right', ios: 'chevron.right', web: 'chevron_right' },
  close: { android: 'close', ios: 'xmark', web: 'close' },
  globe: { android: 'language', ios: 'globe', web: 'language' },
  lock: { android: 'lock', ios: 'lock.shield', web: 'lock' },
  memory: { android: 'library_books', ios: 'books.vertical', web: 'library_books' },
  'memory-fill': {
    android: 'library_books',
    ios: 'books.vertical.fill',
    web: 'library_books',
  },
  message: { android: 'chat_bubble', ios: 'message', web: 'chat_bubble' },
  'message-fill': { android: 'chat_bubble', ios: 'message.fill', web: 'chat_bubble' },
  mic: { android: 'mic', ios: 'mic.fill', web: 'mic' },
  settings: { android: 'settings', ios: 'gearshape', web: 'settings' },
  'settings-fill': { android: 'settings', ios: 'gearshape.fill', web: 'settings' },
  sparkles: { android: 'auto_awesome', ios: 'sparkles', web: 'auto_awesome' },
  stop: { android: 'stop', ios: 'stop.fill', web: 'stop' },
  tasks: { android: 'check_circle', ios: 'checkmark.circle', web: 'check_circle' },
  'tasks-fill': {
    android: 'check_circle',
    ios: 'checkmark.circle.fill',
    web: 'check_circle',
  },
  waveform: { android: 'graphic_eq', ios: 'waveform', web: 'graphic_eq' },
};

export function SystemIcon({ color, name, size = 20 }: SystemIconProps) {
  return (
    <SymbolView
      accessible={false}
      name={symbolNames[name]}
      size={size}
      tintColor={color}
      type="monochrome"
      weight="semibold"
    />
  );
}
