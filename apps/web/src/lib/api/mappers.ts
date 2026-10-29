import type { z } from 'zod';

import type {
  Answer,
  Change,
  Graph,
  Meeting,
  MeetingSummary,
  RelationKind,
  ReviewItem,
} from '../domain/types';
import type * as wire from './schemas';

const minutes = (ms: number | null | undefined) => (ms ? Math.ceil(ms / 60_000) : 0);

export function mapMeetingSummary(item: z.infer<typeof wire.meetingListItem>): MeetingSummary {
  return {
    id: item.id,
    title: item.title,
    state: item.state,
    startedAt: item.created_at,
    durationMin: minutes(item.duration_ms),
    participants: item.people,
    tags: item.topics,
    summary: item.summary ?? '',
    decisionCount: item.decision_count,
    actionCount: item.action_count,
  };
}

export function mapMeeting(
  item: z.infer<typeof wire.meetingDetail>,
  segments: z.infer<typeof wire.segmentPage>['items'],
): Meeting {
  return {
    id: item.id,
    title: item.title,
    state: item.state,
    startedAt: item.created_at,
    durationMin: minutes(item.duration_ms),
    participants: item.insights.people.map((person) => person.label),
    tags: item.insights.topics.map((topic) => topic.label),
    summary: item.summary ?? '',
    decisions: item.insights.decisions.map((decision) => decision.label),
    actions: item.insights.actions.map((action) => ({
      id: action.id,
      text: action.label,
      ...(action.owner_label ? { owner: action.owner_label } : {}),
      ...(action.due_at ? { dueDate: action.due_at } : {}),
    })),
    segments: segments.map((segment) => ({
      id: segment.id,
      startMs: segment.start_ms,
      speaker: segment.speaker_id,
      text: segment.text,
    })),
    speakers: item.speakers.map((speaker) => ({
      id: speaker.id,
      name: speaker.name,
      segments: speaker.segments,
      talkMs: speaker.talk_ms,
    })),
  };
}

export function mapAnswer(value: z.infer<typeof wire.answer>): Answer {
  return {
    answerability: value.answerability,
    text: value.answer,
    degraded: value.degraded,
    citations: value.citations.map((citation) => ({
      meetingId: citation.meeting_id,
      segmentId: citation.segment_id,
      startMs: citation.start_ms,
      speaker: citation.speaker_id,
      quote: citation.quote,
    })),
  };
}

const KINDS: Record<z.infer<typeof wire.subgraph>['edges'][number]['kind'], RelationKind> = {
  MENTIONED_IN: 'mentionedIn',
  ABOUT: 'about',
  DECIDED_IN: 'decidedIn',
  PROPOSED_BY: 'proposedBy',
  ASSIGNED_TO: 'assignedTo',
  DEPENDS_ON: 'dependsOn',
  SUPERSEDES: 'supersedes',
  CONTRADICTS: 'contradicts',
};

export function mapGraph(value: z.infer<typeof wire.subgraph>): Graph {
  return {
    nodes: value.nodes.map((node) => ({
      id: node.id,
      type: node.type,
      label: node.label,
      ...(node.meeting_id ? { meetingId: node.meeting_id } : {}),
    })),
    edges: value.edges.map((edge) => ({
      id: edge.id,
      from: edge.source,
      to: edge.target,
      kind: KINDS[edge.kind],
      ...(edge.status === 'proposed' || edge.status === 'approved' ? { status: edge.status } : {}),
      ...(edge.confidence === undefined ? {} : { confidence: edge.confidence }),
    })),
    truncated: value.truncated,
  };
}

/** The inbox: each proposed edge with the entities at its ends. An edge whose end is missing is dropped. */
export function mapReviews(value: z.infer<typeof wire.subgraph>): ReviewItem[] {
  const nodes = new Map(value.nodes.map((node) => [node.id, node]));
  return value.edges.flatMap((edge) => {
    const from = nodes.get(edge.source);
    const to = nodes.get(edge.target);
    if (!from || !to) return [];
    const meetingId = edge.evidence?.[0]?.meeting_id;
    return [
      {
        id: edge.id,
        kind: KINDS[edge.kind],
        from: { id: from.id, type: from.type, label: from.label },
        to: { id: to.id, type: to.type, label: to.label },
        confidence: edge.confidence ?? 0,
        ...(meetingId ? { meetingId } : {}),
      },
    ];
  });
}

export function mapChange(value: z.infer<typeof wire.changes>['items'][number]): Change {
  return {
    id: value.id,
    newLabel: value.new_label,
    oldLabel: value.old_label,
    meetingId: value.meeting_id,
    date: value.at,
  };
}
