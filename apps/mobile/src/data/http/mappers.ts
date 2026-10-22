import type { Answer, Change, Graph, Meeting, MeetingState, MeetingSummary, RelationKind } from '../types';

type AnyRecord = Record<string, any>;

const duration = (value: number | null | undefined) => (value ? Math.ceil(value / 60_000) : 0);

export function mapMeetingSummary(item: AnyRecord): MeetingSummary {
  return {
    id: item.id,
    title: item.title,
    state: item.state as MeetingState,
    startedAt: item.created_at,
    durationMin: duration(item.duration_ms),
    participants: item.people,
    tags: item.topics,
    summary: item.summary ?? '',
    decisionCount: item.decision_count,
    actionCount: item.action_count,
  };
}

export function mapMeeting(item: AnyRecord, segments: AnyRecord[]): Meeting {
  return {
    id: item.id,
    title: item.title,
    startedAt: item.created_at,
    durationMin: duration(item.duration_ms),
    participants: item.insights.people.map((person: AnyRecord) => person.label),
    tags: item.insights.topics.map((topic: AnyRecord) => topic.label),
    summary: item.summary ?? '',
    decisions: item.insights.decisions.map((decision: AnyRecord) => decision.label),
    actions: item.insights.actions.map((action: AnyRecord) => ({
      id: action.id,
      text: action.label,
      owner: action.owner_label ?? undefined,
      dueDate: action.due_at ?? undefined,
    })),
    segments: segments.map((segment) => ({
      id: segment.id,
      startMs: segment.start_ms,
      speaker: segment.speaker_id,
      text: segment.text,
    })),
  };
}

export function mapAnswer(value: AnyRecord): Answer {
  return {
    answerability: value.answerability,
    text: value.answer,
    citations: value.citations.map((citation: AnyRecord) => ({
      meetingId: citation.meeting_id,
      segmentId: citation.segment_id,
      startMs: citation.start_ms,
      speaker: citation.speaker_id,
      quote: citation.quote,
    })),
  };
}

const KINDS: Record<string, RelationKind> = {
  MENTIONED_IN: 'mentionedIn',
  ABOUT: 'about',
  DECIDED_IN: 'decidedIn',
  PROPOSED_BY: 'proposedBy',
  ASSIGNED_TO: 'assignedTo',
  DEPENDS_ON: 'dependsOn',
  SUPERSEDES: 'supersedes',
  CONTRADICTS: 'contradicts',
};

export function mapGraph(value: AnyRecord): Graph {
  return {
    nodes: value.nodes.map((node: AnyRecord) => ({
      id: node.id,
      type: node.type,
      label: node.label,
      description: undefined,
      meetingId: node.meeting_id ?? undefined,
    })),
    edges: value.edges.map((edge: AnyRecord) => ({
      from: edge.source,
      to: edge.target,
      kind: KINDS[edge.kind] ?? 'mentionedIn',
      status: edge.status,
      confidence: edge.confidence,
    })),
    truncated: value.truncated,
  };
}

export function mapChange(value: AnyRecord): Change {
  return {
    id: value.id,
    newLabel: value.new_label,
    oldLabel: value.old_label,
    meetingId: value.meeting_id,
    date: value.at,
  };
}
