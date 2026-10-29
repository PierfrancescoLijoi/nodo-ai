import { Suspense } from 'react';

import { MeetingDetailScreen } from '@/features/meetings/MeetingDetailScreen';

// The meeting is picked by ?id=: a static export has no page per meeting.
export default function Page() {
  return (
    <Suspense>
      <MeetingDetailScreen />
    </Suspense>
  );
}
