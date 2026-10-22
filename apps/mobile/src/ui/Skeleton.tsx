import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AccessibilityInfo, Animated, Platform, type DimensionValue } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { radius } from '@/theme/tokens';

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    void AccessibilityInfo.isReduceMotionEnabled().then(setReduced);
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduced);
    return () => subscription.remove();
  }, []);
  return reduced;
}

export function Skeleton({ height = 16, width = '100%' }: { height?: number; width?: DimensionValue }) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [opacity] = useState(() => new Animated.Value(0.6));

  useEffect(() => {
    if (reduced) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: Platform.OS !== 'web' }),
        Animated.timing(opacity, { toValue: 0.5, duration: 700, useNativeDriver: Platform.OS !== 'web' }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity, reduced]);

  return (
    <Animated.View
      accessible
      accessibilityLabel={t('common.loading')}
      accessibilityState={{ busy: true }}
      style={{ height, width, borderRadius: radius.control, backgroundColor: colors.surfaceRaised, opacity }}
    />
  );
}
