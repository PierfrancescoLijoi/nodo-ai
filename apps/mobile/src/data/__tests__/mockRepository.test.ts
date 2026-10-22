import { formatClock } from '@/i18n/format';
import { LOCALES } from '@/i18n';

import { createMockRepository } from '../mock/mockRepository';

const codes = LOCALES.map((l) => l.code);

describe.each(codes)('mock repository (%s)', (locale) => {
  const repository = createMockRepository(locale);

  test('every supported answer cites a segment that exists in a meeting', async () => {
    const meetings = await repository.listMeetings();
    for (const questionId of ['ingestionChange', 'saraTasks', 'ragChoice'] as const) {
      const answer = await repository.ask({ questionId, text: '' });
      expect(answer.answerability).toBe('supported');
      expect(answer.citations.length).toBeGreaterThan(0);
      for (const citation of answer.citations) {
        const segment = meetings
          .find((m) => m.id === citation.meetingId)
          ?.segments.find((s) => s.id === citation.segmentId);
        expect(segment?.text).toBe(citation.quote);
      }
    }
  });

  test('an unrelated question is unanswerable and has no citations', async () => {
    const answer = await repository.ask({ text: 'zzz qqq unrelated 12345' });
    expect(answer.answerability).toBe('unanswerable');
    expect(answer.citations).toEqual([]);
  });

  test('every graph edge connects two existing nodes', async () => {
    const graph = await repository.getGraph();
    const ids = new Set(graph.nodes.map((n) => n.id));
    for (const edge of graph.edges) {
      expect(ids.has(edge.from) && ids.has(edge.to)).toBe(true);
    }
  });
});

describe('free-text matching', () => {
  test('routes a Danish question about Sara to her task answer', async () => {
    const answer = await createMockRepository('da').ask({ text: 'Hvad er Saras frist?' });
    expect(answer.citations.map((c) => c.segmentId)).toEqual(['m1-s2']);
  });

  test('returns undefined for a missing meeting', async () => {
    expect(await createMockRepository('en').getMeeting('nope')).toBeUndefined();
  });
});

describe('formatClock', () => {
  test('pads minutes and seconds and clamps negatives', () => {
    expect(formatClock(762)).toBe('12:42');
    expect(formatClock(-5)).toBe('00:00');
  });
});
