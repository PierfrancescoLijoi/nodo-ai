import { Calendar, CheckSquare, DesktopTower, Folder, Hash, SealCheck, User, type Icon } from 'phosphor-react-native';

import type { EntityType } from '@/data/types';

// Entity types are told apart by icon and label, never by colour alone.
export const TYPE_ICONS: Record<EntityType, Icon> = {
  topic: Hash,
  meeting: Calendar,
  decision: SealCheck,
  person: User,
  action: CheckSquare,
  project: Folder,
  system: DesktopTower,
};
