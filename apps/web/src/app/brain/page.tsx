import type { Metadata } from 'next';

import { BrainScreen } from '@/features/brain/BrainScreen';

export const metadata: Metadata = { title: 'Brain' };

export default function Page() {
  return <BrainScreen />;
}
