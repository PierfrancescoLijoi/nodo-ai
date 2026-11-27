import type { Metadata } from 'next';

import { HealthScreen } from '@/features/health/HealthScreen';

export const metadata: Metadata = { title: 'Health' };

export default function Page() {
  return <HealthScreen />;
}
