import { useTranslation } from 'react-i18next';
import { Pressable, View, type ViewStyle } from 'react-native';
import Svg, { Line, Text as SvgText } from 'react-native-svg';

import type { Graph, GraphEdge } from '@/data/types';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, type Colors } from '@/theme/tokens';
import { Text } from '@/ui/Text';

import { TYPE_ICONS } from './typeIcons';
import { radialLayout, type LayoutResult, type PositionedNode } from './layout';

const VIEW_W = 340;
const VIEW_H = 300;
const NODE = 48;
const LABEL_WIDTH = 88;

interface GraphMapProps {
  graph: Graph;
  selected: string;
  onSelect: (id: string) => void;
}

const nodePosition = (node: PositionedNode): ViewStyle => ({
  position: 'absolute',
  left: `${(node.x / VIEW_W) * 100}%`,
  top: `${(node.y / VIEW_H) * 100}%`,
  width: LABEL_WIDTH,
  marginLeft: -LABEL_WIDTH / 2,
  marginTop: -NODE / 2,
  alignItems: 'center',
  gap: 4,
});

const nodeCircle = (colors: Colors, active: boolean): ViewStyle => ({
  width: NODE,
  height: NODE,
  borderRadius: NODE / 2,
  alignItems: 'center',
  justifyContent: 'center',
  borderWidth: active ? 2 : 1,
  borderColor: active ? colors.accent : colors.border,
  backgroundColor: active ? colors.accentSoft : colors.surfaceRaised,
});

function EdgeLayer({ layout, selected }: { layout: LayoutResult; selected: string }) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const byId = new Map(layout.nodes.map((node) => [node.id, node]));
  const points = (edge: GraphEdge) => ({ from: byId.get(edge.from), to: byId.get(edge.to) });
  return (
    <Svg width="100%" height="100%" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} style={{ position: 'absolute' }}>
      {layout.edges.map((edge) => {
        const { from, to } = points(edge);
        if (!from || !to) return null;
        const active = edge.from === selected || edge.to === selected;
        return (
          <Line
            key={`${edge.from}-${edge.kind}-${edge.to}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke={active ? colors.accent : colors.borderStrong}
            strokeWidth={active ? 2 : 1.5}
            strokeDasharray={edge.status === 'proposed' ? '6 4' : undefined}
          />
        );
      })}
      {layout.edges
        .filter((edge) => edge.status === 'proposed')
        .map((edge) => {
          const { from, to } = points(edge);
          if (!from || !to) return null;
          return (
            <SvgText
              key={`status-${edge.from}-${edge.kind}-${edge.to}`}
              x={(from.x + to.x) / 2}
              y={(from.y + to.y) / 2}
              fill={colors.textMuted}
              fontSize="9"
              textAnchor="middle"
            >
              {t('graph.status.proposed')}
            </SvgText>
          );
        })}
    </Svg>
  );
}

function MapNode({
  node,
  selected,
  onSelect,
}: {
  node: PositionedNode;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const active = node.id === selected;
  const NodeIcon = TYPE_ICONS[node.type];
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${node.label}, ${t(`graph.types.${node.type}`)}`}
      accessibilityState={{ selected: active }}
      onPress={() => onSelect(node.id)}
      style={nodePosition(node)}
    >
      <View style={nodeCircle(colors, active)}>
        <NodeIcon size={22} color={active ? colors.accent : colors.textMuted} weight="regular" />
      </View>
      <Text variant="small" style={{ textAlign: 'center' }} numberOfLines={2} maxFontSizeMultiplier={1.2}>
        {node.label}
      </Text>
    </Pressable>
  );
}

// Secondary view: the list is the accessible primary. Nodes are real buttons laid over the SVG edges.
export function GraphMap({ graph, selected, onSelect }: GraphMapProps) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const layout = radialLayout(graph, selected, { width: VIEW_W, height: VIEW_H, maxNodes: 12 });

  return (
    <View style={{ gap: 8 }}>
      <Text variant="small" tone="muted">
        {t('graph.mapLabel')}
      </Text>
      <View
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
        <EdgeLayer layout={layout} selected={selected} />
        {layout.nodes.map((node) => (
          <MapNode key={node.id} node={node} selected={selected} onSelect={onSelect} />
        ))}
      </View>
    </View>
  );
}
