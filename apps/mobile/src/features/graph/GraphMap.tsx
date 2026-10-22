import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';
import Svg, { Line } from 'react-native-svg';

import type { Graph } from '@/data/types';
import { useTheme } from '@/theme/ThemeProvider';
import { radius } from '@/theme/tokens';
import { Text } from '@/ui/Text';

import { TYPE_ICONS } from './typeIcons';

const VIEW_W = 340;
const VIEW_H = 330;
const NODE = 48;

interface GraphMapProps {
  graph: Graph;
  selected: string;
  onSelect: (id: string) => void;
}

// Secondary view: the list is the accessible primary. Nodes are real buttons laid over the SVG edges.
export function GraphMap({ graph, selected, onSelect }: GraphMapProps) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));

  return (
    <View
      accessibilityLabel={t('graph.mapLabel')}
      style={{
        width: '100%',
        aspectRatio: VIEW_W / VIEW_H,
        borderRadius: radius.card,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surface,
        overflow: 'hidden',
      }}
    >
      <Svg width="100%" height="100%" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} style={{ position: 'absolute' }}>
        {graph.edges.map((edge) => {
          const from = byId.get(edge.from);
          const to = byId.get(edge.to);
          if (!from || !to) return null;
          const active = edge.from === selected || edge.to === selected;
          return (
            <Line
              key={`${edge.from}-${edge.kind}-${edge.to}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke={active ? colors.accent : colors.border}
              strokeWidth={active ? 2 : 1.5}
            />
          );
        })}
      </Svg>
      {graph.nodes.map((node) => {
        const active = node.id === selected;
        const NodeIcon = TYPE_ICONS[node.type];
        return (
          <Pressable
            key={node.id}
            accessibilityRole="button"
            accessibilityLabel={`${node.label}, ${t(`graph.types.${node.type}`)}`}
            accessibilityState={{ selected: active }}
            onPress={() => onSelect(node.id)}
            style={{
              position: 'absolute',
              left: `${(node.x / VIEW_W) * 100}%`,
              top: `${(node.y / VIEW_H) * 100}%`,
              width: 96,
              marginLeft: -48,
              marginTop: -NODE / 2,
              alignItems: 'center',
              gap: 4,
            }}
          >
            <View
              style={{
                width: NODE,
                height: NODE,
                borderRadius: NODE / 2,
                alignItems: 'center',
                justifyContent: 'center',
                borderWidth: active ? 2 : 1,
                borderColor: active ? colors.accent : colors.border,
                backgroundColor: active ? colors.accentSoft : colors.surfaceRaised,
              }}
            >
              <NodeIcon size={22} color={active ? colors.accent : colors.textMuted} weight="regular" />
            </View>
            <Text variant="small" style={{ textAlign: 'center' }} numberOfLines={2}>
              {node.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
