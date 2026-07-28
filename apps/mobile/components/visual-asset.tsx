import { Image } from 'expo-image';
import { View, type StyleProp, type ViewStyle } from 'react-native';

type VisualAssetName =
  | 'approvalGuard'
  | 'cortexCore'
  | 'cortexEmblem'
  | 'documentImport'
  | 'readinessSecurity';

type VisualAssetProps = {
  accessibilityLabel?: string;
  name: VisualAssetName;
  size?: number;
  style?: StyleProp<ViewStyle>;
};

const visualAssets = {
  approvalGuard: require('@/assets/images/custom/mongars-approval-guard.png'),
  cortexCore: require('@/assets/images/custom/mongars-cortex-core.png'),
  cortexEmblem: require('@/assets/images/custom/mongars-cortex-emblem-v2.png'),
  documentImport: require('@/assets/images/custom/mongars-document-import.png'),
  readinessSecurity: require('@/assets/images/custom/mongars-readiness-security.png'),
} as const;

export function VisualAsset({
  accessibilityLabel,
  name,
  size = 96,
  style,
}: VisualAssetProps) {
  const accessible = Boolean(accessibilityLabel);

  return (
    <View
      accessible={accessible}
      accessibilityLabel={accessibilityLabel}
      accessibilityRole={accessible ? 'image' : undefined}
      style={[
        {
          height: size,
          width: size,
        },
        style,
      ]}
    >
      <Image
        accessibilityIgnoresInvertColors
        contentFit="contain"
        source={visualAssets[name]}
        style={{ height: '100%', width: '100%' }}
      />
    </View>
  );
}
