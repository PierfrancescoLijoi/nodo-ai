import type { Metadata } from 'next';
import { ModelsScreen } from '@/features/models/ModelsScreen';
export const metadata: Metadata = { title: 'Models' };
export default function Page() {
  return <ModelsScreen />;
}
