import type { Metadata } from 'next';

import { AskScreen } from '@/features/ask/AskScreen';

export const metadata: Metadata = { title: 'Ask' };

export default function Page() {
  return <AskScreen />;
}
