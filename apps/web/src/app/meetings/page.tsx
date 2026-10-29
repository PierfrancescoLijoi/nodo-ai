import type { Metadata } from 'next';

import { MeetingsScreen } from '@/features/meetings/MeetingsScreen';

export const metadata: Metadata = { title: 'Meetings' };

export default function Page() {
  return <MeetingsScreen />;
}
