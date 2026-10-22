// One shape rule for the whole app: cards 16, controls 12, pills for tags and chips only.
export const radius = { control: 12, card: 16, pill: 999 } as const;

export const space = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40 } as const;

export const touch = 48;

export const fonts = {
  regular: 'Geist_400Regular',
  medium: 'Geist_500Medium',
  semibold: 'Geist_600SemiBold',
  mono: 'GeistMono_400Regular',
  monoMedium: 'GeistMono_500Medium',
} as const;

export const type = {
  display: { fontFamily: fonts.semibold, fontSize: 30, lineHeight: 34, letterSpacing: -0.9 },
  title: { fontFamily: fonts.semibold, fontSize: 22, lineHeight: 28, letterSpacing: -0.4 },
  heading: { fontFamily: fonts.semibold, fontSize: 16, lineHeight: 22, letterSpacing: -0.1 },
  body: { fontFamily: fonts.regular, fontSize: 15, lineHeight: 22, letterSpacing: 0 },
  small: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18, letterSpacing: 0 },
  label: { fontFamily: fonts.medium, fontSize: 13, lineHeight: 18, letterSpacing: 0 },
  mono: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 16, letterSpacing: 0 },
} as const;

export type TypeVariant = keyof typeof type;

export interface Colors {
  background: string;
  surface: string;
  surfaceRaised: string;
  border: string;
  borderStrong: string;
  text: string;
  textMuted: string;
  accent: string;
  accentText: string;
  accentSoft: string;
  warning: string;
  warningSoft: string;
  danger: string;
}

// Single accent (teal). Checked ratios: text >= 16:1, muted >= 5.5:1, accent text >= 4.5:1, control borders (borderStrong) >= 3:1.
export const darkColors: Colors = {
  background: '#0B0F14',
  surface: '#121921',
  surfaceRaised: '#19222D',
  border: '#26323F',
  borderStrong: '#5F7083',
  text: '#E9EEF3',
  textMuted: '#9AA8B7',
  accent: '#4FD1C0',
  accentText: '#04211D',
  accentSoft: '#12332F',
  warning: '#E9B65C',
  warningSoft: '#2D2415',
  danger: '#F08A96',
};

export const lightColors: Colors = {
  background: '#F5F7F9',
  surface: '#FFFFFF',
  surfaceRaised: '#EDF1F4',
  border: '#D7DEE5',
  borderStrong: '#76848F',
  text: '#0F1B26',
  textMuted: '#52606F',
  accent: '#0B7A6E',
  accentText: '#FFFFFF',
  accentSoft: '#DDF3EF',
  warning: '#8A5A00',
  warningSoft: '#FBF0D9',
  danger: '#B3283A',
};
