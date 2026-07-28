export const palette = {
  ink: '#16151C',
  paper: '#F7F7FA',
  violet: '#6F5EE8',
  violetLight: '#EEEBFF',
  mint: '#2EB67D',
  amber: '#D9822B',
  rose: '#D94F70',
  night: '#0B0B0F',
  nightSurface: '#15151B',
} as const;

export const appThemes = {
  light: {
    background: palette.paper,
    surface: '#FFFFFF',
    surfaceMuted: '#F0EFF4',
    text: palette.ink,
    textSecondary: '#686572',
    textTertiary: '#716D79',
    primary: palette.violet,
    primaryContrast: '#FFFFFF',
    primarySoft: palette.violetLight,
    positive: '#137D58',
    positiveSoft: '#E2F5ED',
    warning: '#A85C12',
    warningSoft: '#FFF0D9',
    danger: '#B93555',
    dangerSoft: '#FCE6EC',
    border: '#E4E2EA',
    input: '#F2F1F5',
    tabBar: 'rgba(251, 251, 253, 0.96)',
  },
  dark: {
    background: palette.night,
    surface: palette.nightSurface,
    surfaceMuted: '#202028',
    text: '#F6F5F9',
    textSecondary: '#AAA7B4',
    textTertiary: '#85818E',
    primary: '#8D7CFF',
    primaryContrast: '#161225',
    primarySoft: '#27233F',
    positive: '#54D3A1',
    positiveSoft: '#17372C',
    warning: '#F2B56A',
    warningSoft: '#3B2B17',
    danger: '#FF89A4',
    dangerSoft: '#43212C',
    border: '#2B2B34',
    input: '#1B1B22',
    tabBar: 'rgba(13, 13, 18, 0.96)',
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
