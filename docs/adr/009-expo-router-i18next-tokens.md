# ADR-009: Expo Router, i18next and a token-based design system

Status: accepted

## Decision
Navigation by Expo Router (file-based, deep links). Strings by i18next with expo-localization for the device default. Visuals from design tokens (color, type, space, radius, motion) with dark and light themes; one accent colour; Geist and Geist Mono; Phosphor icons. No emoji or hand-drawn glyphs as icons.

## Consequences
- Screens contain no raw colours or user-facing literals.
- The same code renders on Android and in a browser for visual verification.
