import {
  CalendarBlank,
  CheckCircle,
  Cpu,
  Folder,
  Hash,
  ListChecks,
  User,
  type Icon,
} from '@phosphor-icons/react';

import type { EntityType } from '@/lib/domain/types';

export const TYPE_ICONS: Record<EntityType, Icon> = {
  topic: Hash,
  meeting: CalendarBlank,
  decision: CheckCircle,
  person: User,
  action: ListChecks,
  project: Folder,
  system: Cpu,
};
