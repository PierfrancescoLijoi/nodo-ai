import type { Metadata } from 'next';

import { GraphScreen } from '@/features/graph/GraphScreen';

export const metadata: Metadata = { title: 'Graph' };

export default function Page() {
  return <GraphScreen />;
}
