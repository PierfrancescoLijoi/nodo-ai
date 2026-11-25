import type { Metadata } from 'next';

import { WikiScreen } from '@/features/wiki/WikiScreen';

export const metadata: Metadata = { title: 'Wiki' };

export default function Page() {
  return <WikiScreen />;
}
