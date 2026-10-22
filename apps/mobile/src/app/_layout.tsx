import { Geist_400Regular, Geist_500Medium, Geist_600SemiBold, useFonts } from '@expo-google-fonts/geist';
import { GeistMono_400Regular, GeistMono_500Medium } from '@expo-google-fonts/geist-mono';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import '@/i18n';
import { AuthProvider, useAuth } from '@/auth/AuthProvider';
import { RepositoryProvider } from '@/data/RepositoryProvider';
import { ConnectionProvider, useConnection } from '@/connection/ConnectionProvider';
import FirstRunScreen from '@/features/connection/FirstRunScreen';
import LoginScreen from '@/features/auth/LoginScreen';
import { PrefsProvider } from '@/prefs/PrefsProvider';
import { ThemeProvider, useTheme } from '@/theme/ThemeProvider';

void SplashScreen.preventAutoHideAsync();

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

function AuthenticatedApp() {
  const { connection } = useConnection();
  const { status } = useAuth();
  if (connection.mode === 'setup') return <FirstRunScreen />;
  if (status === 'loading') return null;
  if (status === 'signedOut') return <LoginScreen />;
  return (
    <RepositoryProvider>
      <ThemedStack />
    </RepositoryProvider>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Geist_400Regular,
    Geist_500Medium,
    Geist_600SemiBold,
    GeistMono_400Regular,
    GeistMono_500Medium,
  });
  const ready = fontsLoaded || !!fontError;
  useEffect(() => {
    if (ready) void SplashScreen.hideAsync();
  }, [ready]);
  if (!ready) return null;

  return (
    <SafeAreaProvider>
      <PrefsProvider>
        <ThemeProvider>
          <ConnectionProvider>
            <AuthProvider>
              <AuthenticatedApp />
            </AuthProvider>
          </ConnectionProvider>
        </ThemeProvider>
      </PrefsProvider>
    </SafeAreaProvider>
  );
}
