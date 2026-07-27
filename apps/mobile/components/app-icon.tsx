import { SymbolView, type AndroidSymbol, type SFSymbol } from 'expo-symbols';
import type { ColorValue } from 'react-native';

export type AppIconName =
  | 'back'
  | 'chat'
  | 'check'
  | 'chevronRight'
  | 'close'
  | 'document'
  | 'filter'
  | 'globe'
  | 'history'
  | 'lock'
  | 'memory'
  | 'menu'
  | 'microphone'
  | 'paperclip'
  | 'refresh'
  | 'search'
  | 'send'
  | 'settings'
  | 'shield'
  | 'tasks'
  | 'upload';

const iconNames: Record<
  AppIconName,
  { android: AndroidSymbol; ios: SFSymbol; web: AndroidSymbol }
> = {
  back: { android: 'arrow_back', ios: 'chevron.left', web: 'arrow_back' },
  chat: { android: 'chat_bubble', ios: 'bubble.left', web: 'chat_bubble' },
  check: { android: 'check_circle', ios: 'checkmark.circle', web: 'check_circle' },
  chevronRight: {
    android: 'chevron_right',
    ios: 'chevron.right',
    web: 'chevron_right',
  },
  close: { android: 'close', ios: 'xmark', web: 'close' },
  document: { android: 'description', ios: 'doc', web: 'description' },
  filter: {
    android: 'filter_list',
    ios: 'line.3.horizontal.decrease',
    web: 'filter_list',
  },
  globe: { android: 'public', ios: 'globe', web: 'public' },
  history: { android: 'history', ios: 'clock.arrow.circlepath', web: 'history' },
  lock: { android: 'lock', ios: 'lock', web: 'lock' },
  memory: { android: 'menu_book', ios: 'book', web: 'menu_book' },
  menu: { android: 'menu', ios: 'line.3.horizontal', web: 'menu' },
  microphone: { android: 'mic', ios: 'mic', web: 'mic' },
  paperclip: { android: 'attach_file', ios: 'paperclip', web: 'attach_file' },
  refresh: { android: 'refresh', ios: 'arrow.clockwise', web: 'refresh' },
  search: { android: 'search', ios: 'magnifyingglass', web: 'search' },
  send: { android: 'arrow_upward', ios: 'arrow.up', web: 'arrow_upward' },
  settings: { android: 'settings', ios: 'gearshape', web: 'settings' },
  shield: { android: 'shield', ios: 'shield', web: 'shield' },
  tasks: { android: 'checklist', ios: 'checklist', web: 'checklist' },
  upload: { android: 'cloud_upload', ios: 'arrow.up.doc', web: 'cloud_upload' },
};

type AppIconProps = {
  color: ColorValue;
  name: AppIconName;
  size?: number;
};

export function AppIcon({ color, name, size = 20 }: AppIconProps) {
  return (
    <SymbolView
      accessibilityElementsHidden
      name={iconNames[name]}
      size={size}
      tintColor={color}
      weight="regular"
    />
  );
}
