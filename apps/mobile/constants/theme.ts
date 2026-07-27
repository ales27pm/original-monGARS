export const palette = {
  ink: '#17151C',
  paper: '#FBFAFC',
  violet: '#5B2CBF',
  violetLight: '#F1ECFB',
  mint: '#18A558',
  amber: '#B86A16',
  rose: '#C83E62',
  night: '#0E0D13',
  nightSurface: '#191720',
} as const;

export const appThemes = {
  light: {
    background: palette.paper,
    surface: '#FFFFFF',
    surfaceMuted: '#F4F2F6',
    text: palette.ink,
    textSecondary: '#625E69',
    textTertiary: '#8F8A96',
    primary: palette.violet,
    primaryContrast: '#FFFFFF',
    primarySoft: palette.violetLight,
    positive: '#138A4B',
    positiveSoft: '#E9F7EF',
    warning: '#9C5A14',
    warningSoft: '#FFF5E7',
    danger: '#B63152',
    dangerSoft: '#FCEEF2',
    border: '#E7E4EA',
    input: '#F5F3F7',
    tabBar: 'rgba(255, 255, 255, 0.98)',
  },
  dark: {
    background: palette.night,
    surface: palette.nightSurface,
    surfaceMuted: '#25222E',
    text: '#F7F4FC',
    textSecondary: '#B0AABB',
    textTertiary: '#7F798B',
    primary: '#A99CFF',
    primaryContrast: '#171226',
    primarySoft: '#2D284E',
    positive: '#54D3A1',
    positiveSoft: '#17372C',
    warning: '#F2B56A',
    warningSoft: '#3B2B17',
    danger: '#FF89A4',
    dangerSoft: '#43212C',
    border: '#302D39',
    input: '#22202A',
    tabBar: 'rgba(20, 18, 27, 0.96)',
  },
} as const;

export type AppTheme = (typeof appThemes)[keyof typeof appThemes];

export const typography = {
  largeTitle: 28,
  title: 22,
  headline: 16,
  body: 14,
  caption: 11,
} as const;

export const radii = {
  small: 6,
  medium: 8,
  large: 8,
} as const;
