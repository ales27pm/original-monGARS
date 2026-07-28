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
      contentContainerStyle={{
        alignSelf: 'center',
        gap: 12,
        maxWidth: 620,
        padding: 14,
        paddingBottom: 104,
        width: '100%',
      }}
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
