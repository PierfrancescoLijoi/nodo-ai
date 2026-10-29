import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

import type { StoredTokens, TokenStore } from './session';

const KEY = 'nodo.auth.tokens';
let webTokens: StoredTokens | undefined;

export const tokenStore: TokenStore = {
  async get() {
    if (Platform.OS === 'web') return webTokens;
    const raw = await SecureStore.getItemAsync(KEY);
    return raw ? (JSON.parse(raw) as StoredTokens) : undefined;
  },
  async set(tokens) {
    if (Platform.OS === 'web') {
      webTokens = tokens;
      return;
    }
    await SecureStore.setItemAsync(KEY, JSON.stringify(tokens));
  },
  async clear() {
    if (Platform.OS === 'web') {
      webTokens = undefined;
      return;
    }
    await SecureStore.deleteItemAsync(KEY);
  },
};
