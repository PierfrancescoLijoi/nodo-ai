import type { Locale } from '@/i18n';
import { formatDate } from '@/i18n/format';

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
} from '../types';
import { CHANGE_FACTS, CONTENT, FACTS, SECOND_SEGMENT, type LocaleContent } from './content';

const LATENCY_MS = 450;
const pause = (ms = LATENCY_MS) => new Promise<void>((resolve) => setTimeout(resolve, ms));

type MeetingKey = 'm1' | 'm2' | 'm3';

function buildMeetings(content: LocaleContent): Meeting[] {
  return FACTS.map((facts) => {
    const text = content.meetings[facts.id as MeetingKey];
    const segments = [{ ...facts.segment, text: text.quote }];
    if (facts.id === 'm1') segments.push({ ...SECOND_SEGMENT, text: content.m1Quote2 });
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

const EDGES: GraphEdge[] = [
  { from: 'ingestion', to: 'm7', kind: 'mentionedIn' },
  { from: 'ingestion', to: 'm21', kind: 'mentionedIn' },
  { from: 'queue', to: 'ingestion', kind: 'about' },
  { from: 'queue', to: 'm21', kind: 'decidedIn' },
  { from: 'queue', to: 'marco', kind: 'proposedBy' },
  { from: 'queue', to: 'syncFlow', kind: 'supersedes' },
  { from: 'syncFlow', to: 'm7', kind: 'decidedIn' },
  { from: 'benchmark', to: 'sara', kind: 'assignedTo' },
  { from: 'benchmark', to: 'm21', kind: 'mentionedIn' },
];

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

  return {
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
      return graph;
    },
  };
}
