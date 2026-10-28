import { fireEvent, render, screen } from '@testing-library/react-native';

import '@/i18n';
import type { ReviewItem } from '@/data/types';

import { ReviewCard } from '../ReviewCard';

const item: ReviewItem = {
  id: 'r1',
  kind: 'proposedBy',
  from: { id: 'd1', type: 'decision', label: 'Keep the flow synchronous' },
  to: { id: 'p1', type: 'person', label: 'Marco' },
  confidence: 0.876,
  meetingId: 'm1',
};

describe('a proposal waiting for a person', () => {
  it('says in words what it connects, how sure the system was, and offers both answers', async () => {
    await render(<ReviewCard item={item} onDecide={jest.fn()} />);
    expect(screen.getByText('Keep the flow synchronous was proposed by Marco')).toBeTruthy();
    expect(screen.getByText('Confidence 88%')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeTruthy();
  });

  it('reports the decision the person took', async () => {
    const onDecide = jest.fn();
    await render(<ReviewCard item={item} onDecide={onDecide} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Approve' }));
    await fireEvent.press(screen.getByRole('button', { name: 'Reject' }));
    expect(onDecide.mock.calls).toEqual([['approved'], ['rejected']]);
  });

  it('can not be answered twice while the first answer is being saved', async () => {
    const onDecide = jest.fn();
    await render(<ReviewCard item={item} busy onDecide={onDecide} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Approve' }));
    expect(onDecide).not.toHaveBeenCalled();
  });

  it('opens the meeting the proposal came from only when there is one', async () => {
    const open = jest.fn();
    const { rerender } = await render(<ReviewCard item={item} onDecide={jest.fn()} onOpenSource={open} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Open source meeting' }));
    expect(open).toHaveBeenCalledTimes(1);
    await rerender(<ReviewCard item={item} onDecide={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Open source meeting' })).toBeNull();
  });
});
