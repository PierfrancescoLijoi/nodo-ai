import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { useAsync } from '@/data/RepositoryProvider';
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
import { TYPE_ICONS } from './typeIcons';

const TYPE_ORDER: EntityType[] = ['decision', 'topic', 'meeting', 'person', 'action'];

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
      <Text tone="muted">{node.description}</Text>
      <View style={{ gap: space[2] }}>
        <Text variant="label" tone="muted">
          {t('graph.relations', { count: relations.length })}
        </Text>
        {relations.length === 0 ? <Text variant="small">{t('graph.noRelations')}</Text> : null}
        {relations.map((edge) => (
          <Text key={`${edge.from}-${edge.kind}-${edge.to}`} variant="small">
            <Text variant="label" tone={edge.from === nodeId ? 'accent' : 'default'}>
              {labelOf(edge.from)}
            </Text>{' '}
            {t(`graph.relation.${edge.kind}`)}{' '}
            <Text variant="label" tone={edge.to === nodeId ? 'accent' : 'default'}>
              {labelOf(edge.to)}
            </Text>
          </Text>
        ))}
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
  const graph = useAsync((r) => r.getGraph());
  const [view, setView] = useState<'list' | 'map'>('list');
  const [selected, setSelected] = useState('queue');

  return (
    <Screen title={t('graph.title')} intro={t('graph.intro')} right={<SettingsButton />}>
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
        <ErrorNotice />
      ) : graph.status !== 'ready' ? (
        <Skeleton height={240} />
      ) : view === 'list' ? (
        <EntityList graph={graph.data} selected={selected} onSelect={setSelected} />
      ) : (
        <>
          <GraphMap graph={graph.data} selected={selected} onSelect={setSelected} />
          <NodeDetail graph={graph.data} nodeId={selected} />
        </>
      )}
    </Screen>
  );
}
