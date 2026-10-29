import type { Locale } from '@/i18n';
import { formatDate } from '@/i18n/format';

import { ApiError } from '../http/client';
import { validateSpeakerName } from '../speakerName';
import type {
  Answer,
  Citation,
  Graph,
  GraphEdge,
  GraphNode,
  Meeting,
  MeetingSummary,
  MeetingsRepository,
  QuestionId,
  ReviewDecision,
  ReviewItem,
} from '../types';
import { CHANGE_FACTS, CONTENT, FACTS, SECOND_SEGMENT, type LocaleContent } from './content';

const LATENCY_MS = 450;
const pause = (ms = LATENCY_MS) => new Promise<void>((resolve) => setTimeout(resolve, ms));

type MeetingKey = 'm1' | 'm2' | 'm3';

function buildMeetings(content: LocaleContent): Meeting[] {
  return FACTS.map((facts) => {
    const text = content.meetings[facts.id as MeetingKey];
    const segments = [{ ...facts.segment, text: text.quote }];
    segments.push(
      facts.id === 'm1'
        ? { ...SECOND_SEGMENT, text: content.m1Quote2 }
        : { id: `${facts.id}-s2`, startMs: facts.segment.startMs + 30_000, speaker: 'S2', text: text.quote },
    );
    return {
      id: facts.id,
      startedAt: facts.startedAt,
      durationMin: facts.durationMin,
      title: text.title,
      participants: facts.participants,
      tags: facts.tags,
      summary: text.summary,
      decisions: text.decisions,
      actions: [{ id: facts.actionId, text: text.actionText, owner: facts.owner, dueDate: facts.dueDate }],
      segments,
      speakers: [
        { id: 'S1', name: null, segments: 1, talkMs: facts.durationMin * 36_000 },
        { id: 'S2', name: null, segments: 1, talkMs: facts.durationMin * 24_000 },
      ],
      state: 'ready',
    };
  });
}

function toCitations(meetings: Meeting[], cites: [string, string][]): Citation[] {
  return cites.flatMap(([meetingId, segmentId]) => {
    const segment = meetings.find((m) => m.id === meetingId)?.segments.find((s) => s.id === segmentId);
    return segment
      ? [{ meetingId, segmentId, startMs: segment.startMs, speaker: segment.speaker, quote: segment.text }]
      : [];
  });
}

function matchQuestion(text: string, keywords: LocaleContent['keywords']): QuestionId | undefined {
  const lower = text.toLowerCase();
  const scored = (Object.keys(keywords) as QuestionId[]).map((id) => ({
    id,
    score: keywords[id].filter((word) => lower.includes(word)).length,
  }));
  const best = scored.reduce((a, b) => (b.score > a.score ? b : a));
  return best.score > 0 ? best.id : undefined;
}

// Layout is illustrative; a real graph view would use server-provided subgraphs.
const LAYOUT = {
  m7: [55, 45],
  ingestion: [170, 45],
  m21: [285, 45],
  syncFlow: [55, 135],
  queue: [170, 135],
  marco: [55, 225],
  sara: [170, 225],
  benchmark: [285, 225],
} as const;

type LayoutId = keyof typeof LAYOUT;
const at = (id: LayoutId) => ({ x: LAYOUT[id][0], y: LAYOUT[id][1] });

const EDGES: GraphEdge[] = (
  [
    { from: 'ingestion', to: 'm7', kind: 'mentionedIn' },
    { from: 'ingestion', to: 'm21', kind: 'mentionedIn' },
    { from: 'queue', to: 'ingestion', kind: 'about', status: 'proposed', confidence: 0.86 },
    { from: 'queue', to: 'm21', kind: 'decidedIn', status: 'proposed', confidence: 0.92 },
    { from: 'queue', to: 'marco', kind: 'proposedBy', status: 'proposed', confidence: 0.88 },
    { from: 'queue', to: 'syncFlow', kind: 'supersedes', status: 'proposed', confidence: 0.79 },
    { from: 'syncFlow', to: 'm7', kind: 'decidedIn' },
    { from: 'benchmark', to: 'sara', kind: 'assignedTo', status: 'proposed', confidence: 0.81 },
    { from: 'benchmark', to: 'm21', kind: 'mentionedIn' },
  ] satisfies GraphEdge[]
).map((edge) => ({ ...edge, id: `${edge.from}:${edge.kind}:${edge.to}` }));

function buildGraph(content: LocaleContent, meetings: Meeting[], locale: Locale): Graph {
  const meetingNode = (id: 'm21' | 'm7', meetingId: string): GraphNode => {
    const meeting = meetings.find((m) => m.id === meetingId) as Meeting;
    return {
      id,
      type: 'meeting',
      label: formatDate(meeting.startedAt, locale),
      description: meeting.title,
      meetingId,
      ...at(id),
    };
  };
  const node = (id: keyof LocaleContent['graph'], type: GraphNode['type'], meetingId: string): GraphNode => ({
    id,
    type,
    ...content.graph[id],
    meetingId,
    ...at(id),
  });
  return {
    nodes: [
      node('ingestion', 'topic', 'm1'),
      meetingNode('m21', 'm1'),
      meetingNode('m7', 'm3'),
      node('queue', 'decision', 'm1'),
      node('syncFlow', 'decision', 'm3'),
      node('marco', 'person', 'm1'),
      node('sara', 'person', 'm1'),
      node('benchmark', 'action', 'm1'),
    ],
    edges: EDGES,
  };
}

export function createMockRepository(locale: Locale): MeetingsRepository {
  const content = CONTENT[locale];
  const meetings = buildMeetings(content);
  const graph = buildGraph(content, meetings, locale);
  const decisions = new Map<string, ReviewDecision>();
  const waiting = (): ReviewItem[] =>
    graph.edges
      .filter((edge) => edge.status === 'proposed' && edge.id && !decisions.has(edge.id))
      .flatMap((edge) => {
        const from = graph.nodes.find((n) => n.id === edge.from);
        const to = graph.nodes.find((n) => n.id === edge.to);
        if (!from || !to) return [];
        return [
          {
            id: edge.id as string,
            kind: edge.kind,
            from: { id: from.id, type: from.type, label: from.label },
            to: { id: to.id, type: to.type, label: to.label },
            confidence: edge.confidence ?? 0,
            meetingId: from.meetingId,
          },
        ];
      });

  return {
    async createMeeting({ title }) {
      const id = `mock-${Date.now()}-${meetings.length}`;
      meetings.unshift({
        id,
        startedAt: new Date().toISOString(),
        durationMin: 0,
        title: title ?? 'Untitled meeting',
        participants: [],
        tags: [],
        summary: '',
        decisions: [],
        actions: [],
        segments: [],
        speakers: [],
        state: 'uploading',
      });
      return { id };
    },
    async uploadAudio(meetingId, _audio, onProgress) {
      const meeting = meetings.find((item) => item.id === meetingId);
      if (!meeting) throw new ApiError('Meeting not found', 404, 'meeting_not_found');
      onProgress?.(1);
      meeting.state = 'ready';
    },
    async getMeetingState(id) {
      const meeting = meetings.find((item) => item.id === id);
      if (!meeting) throw new ApiError('Meeting not found', 404, 'meeting_not_found');
      return meeting.state;
    },
    async deleteMeeting(id) {
      const index = meetings.findIndex((item) => item.id === id);
      if (index < 0) throw new ApiError('Meeting not found', 404, 'meeting_not_found');
      meetings.splice(index, 1);
    },
    async listMeetings() {
      await pause(120);
      return meetings.map((meeting): MeetingSummary => ({
        id: meeting.id,
        title: meeting.title,
        startedAt: meeting.startedAt,
        durationMin: meeting.durationMin,
        participants: meeting.participants,
        tags: meeting.tags,
        summary: meeting.summary,
        decisionCount: meeting.decisions.length,
        actionCount: meeting.actions.length,
        state: 'ready',
      }));
    },
    async getMeeting(id) {
      await pause(120);
      return meetings.find((m) => m.id === id);
    },
    async nameSpeaker(meetingId, speakerId, name) {
      await pause(120);
      const meeting = meetings.find((item) => item.id === meetingId);
      if (!meeting) throw new ApiError('Meeting not found', 404, 'meeting_not_found');
      if (meeting.state !== 'review' && meeting.state !== 'ready') {
        throw new ApiError('Meeting speakers cannot be edited', 409, 'meeting_not_editable');
      }
      const speaker = meeting.speakers.find((item) => item.id === speakerId);
      if (!speaker) throw new ApiError('Speaker not found', 404, 'speaker_not_found');
      const validationError = validateSpeakerName(name);
      if (validationError) throw new ApiError('Invalid speaker name', 422, validationError);
      speaker.name = name;
      return meeting;
    },
    async listChanges() {
      await pause(120);
      return CHANGE_FACTS.map((fact, index) => ({ ...fact, text: content.changes[index] as string }));
    },
    async ask({ questionId, text }): Promise<Answer> {
      await pause();
      const id = questionId ?? matchQuestion(text, content.keywords);
      if (!id) return { answerability: 'unanswerable', text: content.unanswerable, citations: [] };
      const answer = content.answers[id];
      return { answerability: 'supported', text: answer.text, citations: toCitations(meetings, answer.cites) };
    },
    async getGraph() {
      await pause(120);
      return {
        ...graph,
        edges: graph.edges
          .filter((edge) => !(edge.id && decisions.get(edge.id) === 'rejected'))
          .map((edge) =>
            edge.id && decisions.get(edge.id) === 'approved' ? { ...edge, status: 'approved' as const } : edge,
          ),
      };
    },
    async listReviews() {
      await pause(120);
      return waiting();
    },
    async reviewRelation(id, decision) {
      await pause(120);
      if (!waiting().some((item) => item.id === id)) throw new ApiError('Review not found', 404, 'not_found');
      decisions.set(id, decision);
    },
  };
}
