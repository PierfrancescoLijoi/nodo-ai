// icons.ts imports Phosphor's per-icon source files, which pass a `className` prop to react-native-svg
// (for NativeWind users). react-native-svg's types do not declare it, so declare it here.
import 'react-native-svg';

declare module 'react-native-svg' {
  interface SvgProps {
    className?: string;
  }
}
