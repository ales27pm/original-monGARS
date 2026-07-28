export const palette = {
  ink: '#171422',
  paper: '#F5F4F8',
  violet: '#6654E8',
  violetLight: '#EFEAFF',
  mint: '#2EB67D',
  amber: '#D9822B',
  rose: '#D94F70',
  night: '#0E0D13',
  nightSurface: '#191720',
} as const;

export const appThemes = {
  light: {
    background: palette.paper,
    surface: '#FFFFFF',
    surfaceMuted: '#ECEAF1',
    text: palette.ink,
    textSecondary: '#6E697B',
    textTertiary: '#9691A2',
    primary: palette.violet,
    primaryContrast: '#FFFFFF',
    primarySoft: palette.violetLight,
    positive: '#16865E',
    positiveSoft: '#E2F5ED',
    warning: '#A85C12',
    warningSoft: '#FFF0D9',
    danger: '#B93555',
    dangerSoft: '#FCE6EC',
    border: '#E1DEE8',
    input: '#EFEDF3',
    tabBar: 'rgba(250, 249, 252, 0.96)',
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
  largeTitle: 32,
  title: 22,
  headline: 17,
  body: 15,
  caption: 12,
} as const;

export const radii = {
  small: 10,
  medium: 16,
  large: 24,
} as const;
