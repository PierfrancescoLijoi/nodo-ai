import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import type { LocalEngineApi, LocalStatus } from '@/connection/localApi';
import { space } from '@/theme/tokens';
import { ErrorNotice } from '@/ui/ErrorNotice';
import { SectionHeader } from '@/ui/SectionHeader';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

function missingKey(item: string): 'language' | 'speech' | 'embedding' | 'other' {
  const lower = item.toLowerCase();
  if (lower.includes('language') || lower.includes('gguf')) return 'language';
  if (lower.includes('whisper') || lower.includes('speech') || lower.includes('sherpa')) return 'speech';
  if (lower.includes('embed') || lower.includes('e5')) return 'embedding';
  return 'other';
}

export function EngineSection({ api }: { api: LocalEngineApi }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<LocalStatus>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    const load = () => {
      void api.getStatus().then(
        (next) => {
          if (active) {
            setStatus(next);
            setFailed(false);
          }
        },
        () => active && setFailed(true),
      );
    };
    load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, attempt]);

  return (
    <View style={{ gap: space[3] }}>
      <SectionHeader title={t('settings.engine.title')} />
      {failed ? <ErrorNotice onRetry={() => setAttempt((value) => value + 1)} /> : null}
      {status ? (
        <Surface>
          <Text variant="heading">{t('settings.engine.backend', { name: status.backend })}</Text>
          <Text tone="muted">{t('settings.engine.gpu', { value: t(status.gpu ? 'common.yes' : 'common.no') })}</Text>
          <Text tone="muted">{t('settings.engine.jobs', { count: status.pendingJobs })}</Text>
          {status.missing.length === 0 ? (
            <Text tone="muted">{t('settings.engine.ready')}</Text>
          ) : (
            status.missing.map((item) => (
              <Text key={item} tone="warning">
                {t(`settings.engine.missing.${missingKey(item)}`, { item })}
              </Text>
            ))
          )}
        </Surface>
      ) : null}
    </View>
  );
}
