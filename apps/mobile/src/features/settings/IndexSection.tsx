import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import type { IndexMode, IndexProgress, LocalEngineApi } from '@/connection/localApi';
import { space } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { SectionHeader } from '@/ui/SectionHeader';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

const POLL_MS = 2000;

/** Checks that search and the graph match the archive, and rebuilds what does not (ADR-021). */
export function IndexSection({ api }: { api: LocalEngineApi }) {
  const { t } = useTranslation();
  const [progress, setProgress] = useState<IndexProgress>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (progress?.state !== 'running') return;
    let active = true;
    const timer = setInterval(() => {
      void api.getIndex().then(
        (next) => active && setProgress(next),
        () => active && setFailed(true),
      );
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, progress?.state]);

  const run = (mode: IndexMode) => {
    setFailed(false);
    void api.startIndex(mode).then(setProgress, () => setFailed(true));
  };

  const running = progress?.state === 'running';
  const found = progress?.findings.length ?? 0;
  const done = progress?.state === 'done';

  return (
    <View style={{ gap: space[3] }}>
      <SectionHeader title={t('settings.index.title')} />
      <Surface>
        <Text tone="muted">{t('settings.index.body')}</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space[3] }}>
          <Button
            variant="secondary"
            label={t('settings.index.check')}
            disabled={running}
            onPress={() => run('verify')}
          />
          <Button
            label={t('settings.index.repair')}
            disabled={running || (done && progress?.mode === 'verify' && found === 0)}
            onPress={() => run('repair')}
          />
        </View>
        {failed ? <Text tone="danger">{t('settings.index.error')}</Text> : null}
        {progress ? (
          <View style={{ gap: space[2] }} accessibilityLiveRegion="polite">
            <Text variant="heading">{t(`settings.index.state.${progress.state}`)}</Text>
            {done && progress.mode === 'verify' ? (
              <Text>{found === 0 ? t('settings.index.ok') : t('settings.index.findings', { count: found })}</Text>
            ) : null}
            {done && progress.mode === 'repair' ? (
              <Text>{t('settings.index.repaired', { count: progress.repaired })}</Text>
            ) : null}
            {progress.findings.slice(0, 20).map((finding) => (
              <Text key={`${finding.kind}-${finding.meetingId}`} tone="muted">
                {t(`settings.index.kinds.${finding.kind}`)}
              </Text>
            ))}
            {progress.failed.map((item) => (
              <Text key={`${item.meetingId}-${item.reason}`} tone="danger">
                {t('settings.index.failedMeeting', { id: item.meetingId, reason: item.reason })}
              </Text>
            ))}
          </View>
        ) : null}
      </Surface>
    </View>
  );
}
