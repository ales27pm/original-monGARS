import type { PropsWithChildren } from 'react';
import { ScrollView, type ScrollViewProps } from 'react-native';

import { useAppTheme } from '@/hooks/use-app-theme';

type ScreenScrollProps = PropsWithChildren<
  Pick<ScrollViewProps, 'keyboardDismissMode' | 'keyboardShouldPersistTaps' | 'refreshControl'>
>;

export function ScreenScroll({ children, ...props }: ScreenScrollProps) {
  const theme = useAppTheme();

  return (
    <ScrollView
      contentInsetAdjustmentBehavior="automatic"
      contentContainerStyle={{ padding: 16, paddingBottom: 40, gap: 16 }}
      keyboardDismissMode="interactive"
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
      style={{ flex: 1, backgroundColor: theme.background }}
      {...props}
    >
      {children}
    </ScrollView>
  );
}
