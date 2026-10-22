import {
  Geist_400Regular,
  Geist_500Medium,
  Geist_600SemiBold,
  useFonts,
} from '@expo-google-fonts/geist';
import { GeistMono_400Regular, GeistMono_500Medium } from '@expo-google-fonts/geist-mono';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import '@/i18n';
import { RepositoryProvider } from '@/data/RepositoryProvider';
import { PrefsProvider } from '@/prefs/PrefsProvider';
import { ThemeProvider, useTheme } from '@/theme/ThemeProvider';

function ThemedStack() {
  const { colors, scheme } = useTheme();
  return (
    <>
      <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }}>
        <Stack.Screen name="capture" options={{ presentation: 'modal', animation: 'slide_from_bottom' }} />
      </Stack>
    </>
  );
}

export default function RootLayout() {
  const [fontsLoaded] = useFonts({
    Geist_400Regular,
    Geist_500Medium,
    Geist_600SemiBold,
    GeistMono_400Regular,
    GeistMono_500Medium,
  });
  if (!fontsLoaded) return null;

  return (
    <SafeAreaProvider>
      <PrefsProvider>
        <ThemeProvider>
          <RepositoryProvider>
            <ThemedStack />
          </RepositoryProvider>
        </ThemeProvider>
      </PrefsProvider>
    </SafeAreaProvider>
  );
}
