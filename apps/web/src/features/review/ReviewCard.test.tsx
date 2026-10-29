import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { ReviewItem } from '@/lib/domain/types';
import { renderWithI18n } from '@/test/render';
import { ReviewCard } from './ReviewScreen';

const item: ReviewItem = {
  id: 'e1',
  kind: 'proposedBy',
  from: { id: 'n1', type: 'decision', label: 'Use a queue' },
  to: { id: 'n2', type: 'person', label: 'Marco' },
  confidence: 0.86,
  meetingId: 'm1',
};

const renderCard = (
  props: Partial<{ busy: boolean; onDecide: (d: string) => void }> = {},
  locale: 'en' | 'it' = 'en',
) =>
  renderWithI18n(
    <ul>
      <ReviewCard item={item} busy={props.busy ?? false} onDecide={props.onDecide ?? vi.fn()} />
    </ul>,
    locale,
  );

describe('ReviewCard', () => {
  it('states the proposal as a sentence with how sure the system was', () => {
    renderCard();
    expect(screen.getByRole('heading', { name: 'Use a queue was proposed by Marco' })).toBeInTheDocument();
    expect(screen.getByText('Confidence 86%')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open source meeting' })).toHaveAttribute(
      'href',
      expect.stringContaining('id=m1'),
    );
  });

  it('reports the decision, and each button says which proposal it is about', async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn();
    renderCard({ onDecide });
    await user.click(screen.getByRole('button', { name: /^Approve: Use a queue/ }));
    await user.click(screen.getByRole('button', { name: /^Reject: Use a queue/ }));
    expect(onDecide.mock.calls).toEqual([['approved'], ['rejected']]);
  });

  it('cannot be answered twice while a decision is being saved', () => {
    renderCard({ busy: true });
    expect(screen.getByRole('button', { name: /^Approve/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Reject/ })).toBeDisabled();
  });

  it('speaks the chosen language', () => {
    renderCard({}, 'it');
    expect(screen.getByRole('button', { name: /^Approva/ })).toBeInTheDocument();
  });
});
