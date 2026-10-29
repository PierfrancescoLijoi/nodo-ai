'use client';

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useRepository } from '@/providers/data';
import { SETTLED_STATES, type NewMeeting, type ReviewDecision } from './domain/types';
import type { Locale } from './i18n';

export const keys = {
  meetings: ['meetings'] as const,
  meeting: (id: string) => ['meeting', id] as const,
  meetingState: (id: string) => ['meeting-state', id] as const,
  changes: ['changes'] as const,
  graph: (seedId?: string) => ['graph', seedId ?? null] as const,
  reviews: ['reviews'] as const,
};

export function useMeetings() {
  const repository = useRepository();
  return useQuery({ queryKey: keys.meetings, queryFn: () => repository.listMeetings() });
}

export function useMeeting(id: string | null) {
  const repository = useRepository();
  return useQuery({
    queryKey: keys.meeting(id ?? ''),
    queryFn: () => repository.getMeeting(id ?? ''),
    enabled: id !== null,
  });
}

export function useChanges() {
  const repository = useRepository();
  return useQuery({ queryKey: keys.changes, queryFn: () => repository.listChanges() });
}

export function useGraph(seedId?: string) {
  const repository = useRepository();
  return useQuery({
    queryKey: keys.graph(seedId),
    queryFn: () => repository.getGraph(seedId ? { seedId } : undefined),
    placeholderData: keepPreviousData,
  });
}

export function useReviews() {
  const repository = useRepository();
  return useQuery({ queryKey: keys.reviews, queryFn: () => repository.listReviews() });
}

const POLL_MS = 3000;

/** Polls a recording's state until it is settled (the API asks for every 3 seconds). */
export function useMeetingState(id: string | undefined) {
  const repository = useRepository();
  return useQuery({
    queryKey: keys.meetingState(id ?? ''),
    queryFn: () => repository.getMeetingState(id ?? ''),
    enabled: !!id,
    staleTime: 0,
    refetchInterval: (query) =>
      query.state.data && SETTLED_STATES.includes(query.state.data) ? false : POLL_MS,
  });
}

export function useAsk(locale: Locale) {
  const repository = useRepository();
  return useMutation({ mutationFn: (question: string) => repository.ask(question, locale) });
}

export function useReviewDecision() {
  const repository = useRepository();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: ReviewDecision }) =>
      repository.reviewRelation(id, decision),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.reviews }),
        client.invalidateQueries({ queryKey: ['graph'] }),
        client.invalidateQueries({ queryKey: keys.changes }),
      ]);
    },
  });
}

export function useNameSpeaker(meetingId: string) {
  const repository = useRepository();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ speakerId, name }: { speakerId: string; name: string | null }) =>
      repository.nameSpeaker(meetingId, speakerId, name),
    onSuccess: (meeting) => {
      client.setQueryData(keys.meeting(meetingId), meeting);
      void client.invalidateQueries({ queryKey: keys.meetings });
    },
  });
}

export function useDeleteMeeting() {
  const repository = useRepository();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => repository.deleteMeeting(id),
    onSuccess: async (_unused, id) => {
      client.removeQueries({ queryKey: keys.meeting(id) });
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.meetings }),
        client.invalidateQueries({ queryKey: ['graph'] }),
        client.invalidateQueries({ queryKey: keys.reviews }),
      ]);
    },
  });
}

export function useCreateRecording() {
  const repository = useRepository();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: NewMeeting) => repository.createMeeting(input),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.meetings }),
  });
}
