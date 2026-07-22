import { appThemes } from '@/constants/theme';
import { useColorScheme } from '@/hooks/use-color-scheme';

export function useAppTheme() {
  const colorScheme = useColorScheme();
  return appThemes[colorScheme === 'dark' ? 'dark' : 'light'];
}
