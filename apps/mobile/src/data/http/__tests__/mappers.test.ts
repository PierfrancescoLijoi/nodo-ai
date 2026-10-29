import fixture from './fixtures/api.json';
import { mapAnswer, mapChange, mapGraph, mapMeeting, mapMeetingSummary } from '../mappers';

describe('API mappers', () => {
  it('maps a meeting list item', () => {
    expect(mapMeetingSummary(fixture.meetingListItem)).toEqual({
      id: 'm1',
      title: 'Planning',
      state: 'ready',
      startedAt: '2026-10-21T10:30:00Z',
      durationMin: 2,
      participants: ['Ada'],
      tags: ['Platform'],
      summary: '',
      decisionCount: 2,
      actionCount: 1,
    });
  });

  it('maps meeting details and hides null action metadata', () => {
    const meeting = mapMeeting(fixture.meeting, [fixture.segment]);
    expect(meeting.actions).toEqual([{ id: 'a1', text: 'Test', owner: undefined, dueDate: undefined }]);
    expect(meeting.segments[0]).toMatchObject({ speaker: 'S1', startMs: 1000 });
    expect(meeting.speakers).toEqual([
      { id: 'S1', name: 'Ada', segments: 3, talkMs: 42000 },
      { id: 'S2', name: null, segments: 1, talkMs: 18000 },
    ]);
    expect(meeting.state).toBe('ready');
  });

  it('maps query, graph, and changes', () => {
    expect(mapAnswer(fixture.query).citations[0]?.speaker).toBe('S1');
    expect(mapGraph(fixture.subgraph)).toMatchObject({
      truncated: true,
      edges: [{ kind: 'dependsOn', status: 'proposed' }],
    });
    expect(mapChange(fixture.change)).toEqual({
      id: 'c1',
      newLabel: 'New',
      oldLabel: 'Old',
      meetingId: 'm1',
      date: '2026-10-22T00:00:00Z',
    });
  });
});
