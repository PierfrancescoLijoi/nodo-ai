import type { Metadata } from 'next';

import { ReviewScreen } from '@/features/review/ReviewScreen';

export const metadata: Metadata = { title: 'Review' };

export default function Page() {
  return <ReviewScreen />;
}
