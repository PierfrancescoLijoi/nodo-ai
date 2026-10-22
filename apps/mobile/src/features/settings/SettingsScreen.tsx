import { Check } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { LOCALES } from '@/i18n';
import { useAuth } from '@/auth/AuthProvider';
import { config } from '@/config';
import { usePrefs, type RetentionDays, type ThemePreference } from '@/prefs/PrefsProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space, touch } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { Segmented } from '@/ui/Segmented';
import { SectionHeader } from '@/ui/SectionHeader';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

const THEMES: ThemePreference[] = ['system', 'light', 'dark'];
const RETENTION: RetentionDays[] = [7, 30, 90];

function LanguagePicker() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const { locale, setLocale } = usePrefs();
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={t('settings.language')} style={{ gap: space[2] }}>
      {LOCALES.map(({ code, nativeName }) => {
        const selected = code === locale;
        return (
          <Pressable
            key={code}
            accessibilityRole="radio"
            accessibilityState={{ checked: selected, selected }}
            accessibilityLanguage={code}
            onPress={() => setLocale(code)}
            style={{
              minHeight: touch,
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingHorizontal: space[4],
              borderRadius: radius.control,
              borderWidth: selected ? 2 : 1,
              borderColor: selected ? colors.accent : colors.border,
              backgroundColor: selected ? colors.accentSoft : colors.surface,
            }}
          >
            <Text variant="heading">{nativeName}</Text>
            {selected ? <Check size={20} color={colors.accent} weight="bold" /> : null}
          </Pressable>
        );
      })}
    </View>
  );
}

function DataRow({ title, body }: { title: string; body: string }) {
  const { t } = useTranslation();
  return (
    <Surface>
      <Text variant="heading">{title}</Text>
      <Text variant="small" tone="muted">
        {body}
      </Text>
      <Button variant="secondary" disabled label={t('settings.notAvailable')} onPress={() => undefined} />
    </Surface>
  );
}

export default function SettingsScreen() {
  const { t } = useTranslation();
  const { status, signOut } = useAuth();
  const { themePreference, setThemePreference, retentionDays, setRetentionDays } = usePrefs();

  return (
    <Screen back title={t('settings.title')} intro={t('settings.intro')} bottomInset>
      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('settings.account')} />
        <Surface>
          <Text variant="heading">{t('settings.dataSource')}</Text>
          <Text tone="muted">
            {t(config.dataSource === 'cloud' ? 'settings.dataSourceCloud' : 'settings.dataSourceMock')}
          </Text>
          <Text tone="muted">{t('settings.signedIn')}</Text>
          {config.dataSource === 'cloud' && status === 'signedIn' ? (
            <Button variant="secondary" label={t('settings.signOut')} onPress={() => void signOut()} />
          ) : null}
        </Surface>
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('settings.language')} />
        <LanguagePicker />
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('settings.theme')} />
        <Segmented
          label={t('settings.theme')}
          value={themePreference}
          onChange={setThemePreference}
          options={THEMES.map((value) => ({ value, label: t(`settings.themeOptions.${value}`) }))}
        />
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('settings.retention')} />
        <Segmented
          label={t('settings.retention')}
          value={String(retentionDays)}
          onChange={(value) => setRetentionDays(Number(value) as RetentionDays)}
          options={RETENTION.map((days) => ({
            value: String(days),
            label: t('capture.retentionOption', { count: days }),
          }))}
        />
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('settings.data')} />
        <DataRow title={t('settings.export.title')} body={t('settings.export.body')} />
        <DataRow title={t('settings.erase.title')} body={t('settings.erase.body')} />
      </View>

      <Surface tone="warning">
        <Text variant="heading" tone="warning">
          {t('settings.legalTitle')}
        </Text>
        <Text variant="small">{t('settings.legalBody')}</Text>
      </Surface>
    </Screen>
  );
}
