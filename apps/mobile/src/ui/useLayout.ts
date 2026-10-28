import { useWindowDimensions } from 'react-native';

// Below this width the app is the single column it was designed as; above it, it is a desktop window.
export const DESKTOP_MIN_WIDTH = 1024;

export function useLayout(): { isDesktop: boolean; width: number } {
  const { width } = useWindowDimensions();
  return { isDesktop: width >= DESKTOP_MIN_WIDTH, width };
}
