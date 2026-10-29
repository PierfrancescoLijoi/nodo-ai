import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { useAsync } from '@/data/RepositoryProvider';
import { useConnection } from '@/connection/ConnectionProvider';
import type { EntityType, Graph } from '@/data/types';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space, touch } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { Segmented } from '@/ui/Segmented';
import { SettingsButton } from '@/ui/SettingsButton';
import { ErrorNotice } from '@/ui/ErrorNotice';
import { Skeleton } from '@/ui/Skeleton';
import { Surface } from '@/ui/Surface';
import { Tag } from '@/ui/Tag';
import { Text } from '@/ui/Text';

import { GraphMap } from './GraphMap';
import { radialLayout } from './layout';
import { TYPE_ICONS } from './typeIcons';

const TYPE_ORDER: EntityType[] = ['decision', 'topic', 'project', 'system', 'meeting', 'person', 'action'];

function ReviewLink() {
  const { t } = useTranslation();
  const router = useRouter();
  const reviews = useAsync((r) => r.listReviews(), []);
  if (reviews.status !== 'ready' || reviews.data.length === 0) return null;
  return (
    <Surface tone="accent">
      <Text variant="heading">{t('review.pending', { count: reviews.data.length })}</Text>
      <Button variant="secondary" label={t('review.open')} onPress={() => router.push('/review')} />
    </Surface>
  );
}

function NodeDetail({ graph, nodeId }: { graph: Graph; nodeId: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const node = graph.nodes.find((n) => n.id === nodeId);
  if (!node) return null;
  const labelOf = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  const relations = graph.edges.filter((e) => e.from === nodeId || e.to === nodeId);

  return (
    <Surface>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space[3] }}>
        <Text variant="heading" style={{ flexShrink: 1 }}>
          {node.label}
        </Text>
        <Tag label={t(`graph.types.${node.type}`)} tone="accent" />
      </View>
      {node.description ? <Text tone="muted">{node.description}</Text> : null}
      <View style={{ gap: space[2] }}>
        <Text variant="label" tone="muted">
          {t('graph.relations', { count: relations.length })}
        </Text>
        {relations.length === 0 ? <Text variant="small">{t('graph.noRelations')}</Text> : null}
        {relations.map((edge) => {
          const relation = t(`graph.relation.${edge.kind}`, { from: labelOf(edge.from), to: labelOf(edge.to) });
          const status = edge.status ? t(`graph.status.${edge.status}`) : undefined;
          return (
            <Text key={`${edge.from}-${edge.kind}-${edge.to}`} variant="small">
              {[relation, status].filter(Boolean).join(' - ')}
            </Text>
          );
        })}
      </View>
      {node.meetingId ? (
        <Button
          variant="secondary"
          label={t('graph.openSource')}
          onPress={() => router.push({ pathname: '/meeting/[id]', params: { id: node.meetingId as string } })}
        />
      ) : null}
    </Surface>
  );
}

function EntityList({ graph, selected, onSelect }: { graph: Graph; selected: string; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  return (
    <View style={{ gap: space[5] }}>
      {TYPE_ORDER.map((type) => {
        const nodes = graph.nodes.filter((n) => n.type === type);
        if (nodes.length === 0) return null;
        const TypeIcon = TYPE_ICONS[type];
        return (
          <View key={type} style={{ gap: space[2] }}>
            <Text variant="label" tone="muted" accessibilityRole="header">
              {t(`graph.types.${type}`)}
            </Text>
            {nodes.map((node) => {
              const active = node.id === selected;
              return (
                <View key={node.id} style={{ gap: space[2] }}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    onPress={() => onSelect(node.id)}
                    style={{
                      minHeight: touch,
                      flexDirection: 'row',
                      alignItems: 'center',
                      gap: space[3],
                      paddingHorizontal: space[4],
                      borderRadius: radius.control,
                      borderWidth: active ? 2 : 1,
                      borderColor: active ? colors.accent : colors.border,
                      backgroundColor: active ? colors.accentSoft : colors.surface,
                    }}
                  >
                    <TypeIcon size={20} color={active ? colors.accent : colors.textMuted} weight="regular" />
                    <Text variant="heading">{node.label}</Text>
                  </Pressable>
                  {active ? <NodeDetail graph={graph} nodeId={node.id} /> : null}
                </View>
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

export default function GraphScreen() {
  const { t } = useTranslation();
  const { connection } = useConnection();
  const [seedId, setSeedId] = useState<string>();
  const graph = useAsync((r) => r.getGraph(seedId ? { seedId } : undefined), [seedId]);
  const [view, setView] = useState<'list' | 'map'>('list');
  const [selected, setSelected] = useState('queue');
  const effectiveSelected =
    graph.status === 'ready' && graph.data.nodes.some((node) => node.id === selected)
      ? selected
      : graph.status === 'ready'
        ? (graph.data.nodes[0]?.id ?? '')
        : selected;
  const select = (id: string) => {
    setSelected(id);
    if (connection.mode !== 'mock') setSeedId(id);
  };

  return (
    <Screen title={t('graph.title')} intro={t('graph.intro')} right={<SettingsButton />}>
      <ReviewLink />
      <Segmented
        label={t('graph.viewLabel')}
        value={view}
        onChange={setView}
        options={[
          { value: 'list', label: t('graph.viewList') },
          { value: 'map', label: t('graph.viewMap') },
        ]}
      />
      {graph.status === 'error' ? (
        <ErrorNotice onRetry={graph.retry} />
      ) : graph.status !== 'ready' ? (
        <Skeleton height={240} />
      ) : view === 'list' ? (
        <>
          {graph.data.truncated ? <Text tone="muted">{t('graph.truncated')}</Text> : null}
          <EntityList graph={graph.data} selected={effectiveSelected} onSelect={select} />
        </>
      ) : (
        <>
          {graph.data.truncated ||
          radialLayout(graph.data, effectiveSelected, { width: 340, height: 300, maxNodes: 12 }).leftOut.length > 0 ? (
            <Text tone="muted">{t('graph.truncated')}</Text>
          ) : null}
          <GraphMap graph={graph.data} selected={effectiveSelected} onSelect={select} />
          <NodeDetail graph={graph.data} nodeId={effectiveSelected} />
        </>
      )}
    </Screen>
  );
}
