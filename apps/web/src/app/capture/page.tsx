import type { Metadata } from 'next';

import { CaptureScreen } from '@/features/capture/CaptureScreen';

export const metadata: Metadata = { title: 'New recording' };

export default function Page() {
  return <CaptureScreen />;
}
