import { Text, View } from 'react-native';

import { useAppTheme } from '@/hooks/use-app-theme';

type SectionHeadingProps = {
  detail?: string;
  title: string;
};

export function SectionHeading({ detail, title }: SectionHeadingProps) {
  const theme = useAppTheme();

  return (
    <View style={{ paddingHorizontal: 4, gap: 3 }}>
      <Text selectable style={{ color: theme.text, fontSize: 20, fontWeight: '700' }}>
        {title}
      </Text>
      {detail ? (
        <Text selectable style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 19 }}>
          {detail}
        </Text>
      ) : null}
    </View>
  );
}
