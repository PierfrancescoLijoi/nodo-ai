// What the API sends, checked at the boundary. A response that does not match is an error the screen can
// show, not an `undefined` three components deeper. Unknown fields are ignored so the API can grow.
import { z } from 'zod';

const state = z.enum(['uploading', 'transcribing', 'extracting', 'review', 'ready', 'failed']);

export const meetingListItem = z.object({
  id: z.string(),
  title: z.string(),
  state,
  created_at: z.string(),
  duration_ms: z.number().nullable().optional(),
  summary: z.string().nullable().optional(),
  people: z.array(z.string()).default([]),
  topics: z.array(z.string()).default([]),
  decision_count: z.number().default(0),
  action_count: z.number().default(0),
});

export const meetingPage = z.object({
  items: z.array(meetingListItem),
  next_cursor: z.string().nullable().optional(),
});

const labelled = z.object({ id: z.string(), label: z.string() });

export const meetingDetail = z.object({
  id: z.string(),
  title: z.string(),
  state,
  created_at: z.string(),
  duration_ms: z.number().nullable().optional(),
  summary: z.string().nullable().optional(),
  insights: z.object({
    people: z.array(labelled),
    topics: z.array(labelled),
    decisions: z.array(labelled),
    actions: z.array(
      z.object({
        id: z.string(),
        label: z.string(),
        owner_label: z.string().nullable().optional(),
        due_at: z.string().nullable().optional(),
      }),
    ),
  }),
  speakers: z.array(
    z.object({
      id: z.string(),
      name: z.string().nullable(),
      segments: z.number(),
      talk_ms: z.number(),
    }),
  ),
});

export const segmentPage = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      speaker_id: z.string(),
      start_ms: z.number(),
      text: z.string(),
    }),
  ),
});

export const createdMeeting = z.object({ id: z.string() });
export const uploadSession = z.object({ upload_url: z.string().min(1) });
export const meetingStateOnly = z.object({ state });

export const answer = z.object({
  answer: z.string(),
  answerability: z.enum(['supported', 'partial', 'unanswerable']),
  degraded: z.boolean().default(false),
  citations: z.array(
    z.object({
      meeting_id: z.string(),
      segment_id: z.string(),
      speaker_id: z.string(),
      start_ms: z.number(),
      quote: z.string(),
    }),
  ),
});

const entityType = z.enum(['topic', 'meeting', 'decision', 'person', 'action', 'project', 'system']);
const relationKind = z.enum([
  'MENTIONED_IN',
  'ABOUT',
  'DECIDED_IN',
  'PROPOSED_BY',
  'ASSIGNED_TO',
  'DEPENDS_ON',
  'SUPERSEDES',
  'CONTRADICTS',
]);

export const subgraph = z.object({
  nodes: z.array(
    z.object({
      id: z.string(),
      type: entityType,
      label: z.string(),
      meeting_id: z.string().nullable().optional(),
    }),
  ),
  edges: z.array(
    z.object({
      id: z.string(),
      source: z.string(),
      target: z.string(),
      kind: relationKind,
      status: z.enum(['proposed', 'approved', 'rejected']).nullable().optional(),
      confidence: z.number().optional(),
      evidence: z.array(z.object({ meeting_id: z.string() })).optional(),
    }),
  ),
  truncated: z.boolean().default(false),
});

export const changes = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      new_label: z.string(),
      old_label: z.string(),
      meeting_id: z.string(),
      at: z.string(),
    }),
  ),
});

export const exportJob = z.object({
  job_id: z.string(),
  status: z.enum(['queued', 'running', 'ready', 'failed']),
});

const progress = z.enum(['idle', 'running', 'done', 'failed']);
const failedItem = z.object({ meeting_id: z.string(), reason: z.string() });

export const localStatus = z.object({
  backend: z.string(),
  missing: z.array(z.string()),
  pending_jobs: z.number(),
  gpu: z.boolean(),
});

export const syncProgress = z.object({
  state: progress,
  total: z.number(),
  done: z.number(),
  pushed: z.number(),
  unchanged: z.number(),
  deleted: z.number(),
  failed: z.array(failedItem),
  message: z.string(),
});

export const indexProgress = z.object({
  state: progress,
  mode: z.string(),
  checked: z.number(),
  skipped: z.number(),
  repaired: z.number(),
  findings: z.array(z.object({ kind: z.string(), meeting_id: z.string() })),
  failed: z.array(failedItem),
  message: z.string(),
});
