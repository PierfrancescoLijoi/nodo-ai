// The shapes the screens need. The wire contract is packages/contracts (OpenAPI); mappers translate.
import type { Locale } from '../i18n';

export interface Segment {
  id: string;
  startMs: number;
  speaker: string;
  text: string;
}

export interface Speaker {
  id: string;
  name: string | null;
  segments: number;
  talkMs: number;
}

export interface ActionItem {
  id: string;
  text: string;
  owner?: string;
  dueDate?: string;
}

export type MeetingState = 'uploading' | 'transcribing' | 'extracting' | 'review' | 'ready' | 'failed';

export const SETTLED_STATES: readonly MeetingState[] = ['review', 'ready', 'failed'];

export interface MeetingSummary {
  id: string;
  title: string;
  startedAt: string;
  durationMin: number;
  participants: string[];
  tags: string[];
  summary: string;
  decisionCount: number;
  actionCount: number;
  state: MeetingState;
}

export interface Meeting {
  id: string;
  startedAt: string;
  durationMin: number;
  title: string;
  participants: string[];
  tags: string[];
  summary: string;
  decisions: string[];
  actions: ActionItem[];
  segments: Segment[];
  speakers: Speaker[];
  state: MeetingState;
}

export interface Citation {
  meetingId: string;
  segmentId: string;
  startMs: number;
  speaker: string;
  quote: string;
}

export type Answerability = 'supported' | 'partial' | 'unanswerable';

export interface Answer {
  answerability: Answerability;
  text: string;
  citations: Citation[];
  /** The language model was unreachable: `text` says so and the citations are sources, not an answer. */
  degraded: boolean;
}

export type EntityType = 'topic' | 'meeting' | 'decision' | 'person' | 'action' | 'project' | 'system';

export type RelationKind =
  | 'mentionedIn'
  | 'about'
  | 'decidedIn'
  | 'proposedBy'
  | 'supersedes'
  | 'assignedTo'
  | 'dependsOn'
  | 'contradicts';

export interface GraphNode {
  id: string;
  type: EntityType;
  label: string;
  meetingId?: string;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  kind: RelationKind;
  status?: 'proposed' | 'approved';
  confidence?: number;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export interface Change {
  id: string;
  newLabel: string;
  oldLabel: string;
  date: string;
  meetingId: string;
}

export type ReviewDecision = 'approved' | 'rejected';

export interface ReviewEnd {
  id: string;
  type: EntityType;
  label: string;
}

/** A link the system proposed and nobody has decided yet (a decision stays a proposal until approved). */
export interface ReviewItem {
  id: string;
  kind: RelationKind;
  from: ReviewEnd;
  to: ReviewEnd;
  confidence: number;
  meetingId?: string;
}

export type RetentionDays = 7 | 30 | 90;

export interface NewMeeting {
  title?: string;
  locale: Locale;
  retentionDays: RetentionDays;
}

export type ExportStatus = 'queued' | 'running' | 'ready' | 'failed';

export interface ExportJob {
  id: string;
  status: ExportStatus;
}
