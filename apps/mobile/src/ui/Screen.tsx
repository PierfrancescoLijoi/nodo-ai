import { useRouter } from 'expo-router';
import { CaretLeft } from '@/ui/icons';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyboardAvoidingView, Pressable, ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useTheme } from '@/theme/ThemeProvider';
import { space, touch } from '@/theme/tokens';

import { Text } from './Text';

const MAX_WIDTH = 640;

interface ScreenProps {
  title?: string;
  intro?: string;
  back?: boolean;
  right?: ReactNode;
  wordmark?: boolean;
  bottomInset?: boolean;
  children: ReactNode;
}

export function Screen({ title, intro, back, right, wordmark, bottomInset, children }: ScreenProps) {
  const { colors } = useTheme();
  const router = useRouter();
  const { t } = useTranslation();

  return (
    <SafeAreaView
      edges={bottomInset ? ['top', 'bottom'] : ['top']}
      style={{ flex: 1, backgroundColor: colors.background }}
    >
      <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={{
            width: '100%',
            maxWidth: MAX_WIDTH,
            alignSelf: 'center',
            paddingHorizontal: space[5],
            paddingTop: space[3],
            paddingBottom: space[10],
            gap: space[6],
          }}
          keyboardShouldPersistTaps="handled"
        >
          <View style={{ gap: space[3] }}>
            <View
              style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: touch }}
            >
              {back ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={t('common.back')}
                  onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))}
                  style={{
                    minHeight: touch,
                    flexDirection: 'row',
                    alignItems: 'center',
                    gap: space[1],
                    marginLeft: -space[2],
                    paddingHorizontal: space[2],
                  }}
                >
                  <CaretLeft size={22} color={colors.text} weight="bold" />
                  <Text variant="label">{t('common.back')}</Text>
                </Pressable>
              ) : wordmark ? (
                <Text variant="heading" style={{ letterSpacing: 3 }}>
                  {t('app.name')}
                </Text>
              ) : (
                <View />
              )}
              {right}
            </View>
            {title ? (
              <Text variant="display" accessibilityRole="header">
                {title}
              </Text>
            ) : null}
            {intro ? <Text tone="muted">{intro}</Text> : null}
          </View>
          {children}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
