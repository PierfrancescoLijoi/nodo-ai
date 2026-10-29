# ADR-001: React Native with a native foreground recorder

Status: accepted

## Context
The team is full-stack JS and Python. Capture needs a reliable Android foreground service of type microphone, which Expo Go cannot provide.

## Decision
Expo (React Native, TypeScript) with a development build. A small native module owns recording, chunk encryption and the foreground notification, exposed to JS through a port (`Recorder`).

## Consequences
- Most UI code is shared and testable in a browser via react-native-web.
- The native module is the only Kotlin surface; it is replaceable if policy or OS changes.
- Kotlin plus Compose stays the fallback if React Native cannot meet capture reliability.
