// Mirrors the contract in packages/contracts (OpenAPI) in the shapes the UI needs.
export interface Segment {
  id: string;
  startMs: number;
  speaker: string;
  text: string;
}

export interface ActionItem {
  id: string;
  text: string;
  owner: string;
  dueDate: string;
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
}

export interface Citation {
  meetingId: string;
  segmentId: string;
  startMs: number;
  speaker: string;
  quote: string;
}

export type Answerability = 'supported' | 'unanswerable';

export interface Answer {
  answerability: Answerability;
  text: string;
  citations: Citation[];
}

export type QuestionId = 'ingestionChange' | 'saraTasks' | 'ragChoice';

export type EntityType = 'topic' | 'meeting' | 'decision' | 'person' | 'action';

export type RelationKind =
  | 'mentionedIn'
  | 'about'
  | 'decidedIn'
  | 'proposedBy'
  | 'supersedes'
  | 'assignedTo';

export interface GraphNode {
  id: string;
  type: EntityType;
  label: string;
  description: string;
  meetingId?: string;
  x: number;
  y: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  kind: RelationKind;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Change {
  id: string;
  text: string;
  date: string;
  meetingId: string;
}

export interface MeetingsRepository {
  listMeetings(): Promise<Meeting[]>;
  getMeeting(id: string): Promise<Meeting | undefined>;
  listChanges(): Promise<Change[]>;
  ask(input: { questionId?: QuestionId; text: string }): Promise<Answer>;
  getGraph(): Promise<Graph>;
}
