import { useEffect, useState } from 'react';
import { Animated, Platform, View } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { useReducedMotion } from '@/ui/Skeleton';

const BARS = 28;
const HEIGHTS = Array.from(
  { length: BARS },
  (_, i) => 10 + Math.round(34 * Math.abs(Math.sin(i * 1.43) * Math.cos(i * 0.21))),
);

function Bar({ index, height, active, animate }: { index: number; height: number; active: boolean; animate: boolean }) {
  const { colors } = useTheme();
  const [scale] = useState(() => new Animated.Value(0.5));

  useEffect(() => {
    if (!active || !animate) {
      scale.setValue(active ? 1 : 0.4);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(scale, {
          toValue: 1,
          duration: 500 + (index % 7) * 70,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(scale, {
          toValue: 0.4,
          duration: 500 + (index % 5) * 90,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [active, animate, index, scale]);

  return (
    <Animated.View
      style={{
        width: 4,
        height,
        borderRadius: 2,
        backgroundColor: colors.accent,
        opacity: active ? 1 : 0.4,
        transform: [{ scaleY: scale }],
      }}
    />
  );
}

// Decorative: it signals "recording is live" and carries no audio data in this build.
export function LevelBars({ active }: { active: boolean }) {
  const reduced = useReducedMotion();
  return (
    <View
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 3, height: 56 }}
    >
      {HEIGHTS.map((height, index) => (
        <Bar key={index} index={index} height={height} active={active} animate={!reduced} />
      ))}
    </View>
  );
}
