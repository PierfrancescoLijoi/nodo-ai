'use client';

import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, Segmented, SectionHeader, Surface } from '@/components/ui/primitives';
import type { RetentionDays } from '@/lib/domain/types';
import { LOCALES } from '@/lib/i18n';
import { useAuth } from '@/providers/auth';
import { useConnection } from '@/providers/connection';
import { useEngine } from '@/providers/data';
import { usePrefs, type ThemePreference } from '@/providers/prefs';
import styles from '../features.module.css';
import { AzureSyncSection } from './AzureSyncSection';
import { EngineSection } from './EngineSection';
import { ExportSection } from './ExportSection';
import { IndexSection } from './IndexSection';

const THEMES: ThemePreference[] = ['system', 'light', 'dark'];
const RETENTION: RetentionDays[] = [7, 30, 90];

function LanguagePicker() {
  const { t } = useTranslation();
  const { locale, setLocale } = usePrefs();
  return (
    <div role="radiogroup" aria-label={t('settings.language')} className={styles.stack}>
      {LOCALES.map(({ code, nativeName }) => (
        <button
          key={code}
          type="button"
          role="radio"
          aria-checked={code === locale}
          lang={code}
          className={styles.choice}
          onClick={() => setLocale(code)}
        >
          {nativeName}
        </button>
      ))}
    </div>
  );
}

export function SettingsScreen() {
  const { t } = useTranslation();
  const connection = useConnection();
  const auth = useAuth();
  const engine = useEngine();
  const { theme, setTheme, retentionDays, setRetentionDays } = usePrefs();

  return (
    <>
      <PageHeader title={t('settings.title')} intro={t('settings.intro')} />

      <section className={styles.section}>
        <SectionHeader title={t('settings.account')} />
        <Surface>
          <h3>{t('settings.dataSource')}</h3>
          <p className={styles.muted}>
            {t(connection.mode === 'azure' ? 'settings.dataSourceCloud' : 'settings.dataSourceLocal')}
          </p>
          {connection.mode === 'azure' && auth.status === 'signedIn' ? (
            <>
              <p className={styles.muted}>{t('settings.signedIn')}</p>
              <div>
                <Button variant="secondary" onClick={auth.signOut}>
                  {t('settings.signOut')}
                </Button>
              </div>
            </>
          ) : null}
        </Surface>
      </section>

      {engine ? (
        <>
          <EngineSection engine={engine} />
          <IndexSection engine={engine} />
          <AzureSyncSection engine={engine} />
        </>
      ) : null}

      <section className={styles.section}>
        <SectionHeader title={t('settings.language')} />
        <LanguagePicker />
      </section>

      <section className={styles.section}>
        <SectionHeader title={t('settings.theme')} />
        <Segmented
          label={t('settings.theme')}
          value={theme}
          onChange={setTheme}
          options={THEMES.map((value) => ({ value, label: t(`settings.themeOptions.${value}`) }))}
        />
      </section>

      <section className={styles.section}>
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
      </section>

      <ExportSection />

      <Surface tone="warning" as="section" aria-labelledby="legal-title">
        <h3 id="legal-title" className={styles.warningText}>
          {t('settings.legalTitle')}
        </h3>
        <p className={styles.small}>{t('settings.legalBody')}</p>
      </Surface>
    </>
  );
}
