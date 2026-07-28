import { Text, View } from 'react-native';

import { useAppTheme } from '@/hooks/use-app-theme';

type SectionHeadingProps = {
  detail?: string;
  level?: 'screen' | 'section';
  title: string;
};

export function SectionHeading({ detail, level = 'section', title }: SectionHeadingProps) {
  const theme = useAppTheme();
  const screen = level === 'screen';

  return (
    <View style={{ gap: 3 }}>
      <Text
        selectable
        style={{
          color: screen ? theme.text : theme.primary,
          fontSize: screen ? 21 : 13,
          fontWeight: '700',
        }}
      >
        {title}
      </Text>
      {detail ? (
        <Text selectable style={{ color: theme.textSecondary, fontSize: 11, lineHeight: 16 }}>
          {detail}
        </Text>
      ) : null}
    </View>
  );
}
