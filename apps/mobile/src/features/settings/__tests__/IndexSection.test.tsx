import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import '@/i18n';
import type { IndexProgress, LocalEngineApi } from '@/connection/localApi';

import { IndexSection } from '../IndexSection';

const done = (over: Partial<IndexProgress>): IndexProgress => ({
  state: 'done',
  mode: 'verify',
  checked: 3,
  skipped: 0,
  repaired: 0,
  findings: [],
  failed: [],
  message: '',
  ...over,
});

function engine(overrides: Partial<LocalEngineApi>): LocalEngineApi {
  return {
    getStatus: jest.fn(),
    startSync: jest.fn(),
    getSync: jest.fn(),
    startIndex: jest.fn(),
    getIndex: jest.fn(),
    ...overrides,
  };
}

describe('checking and repairing the search index', () => {
  it('says everything matches when nothing differs, and then has nothing to repair', async () => {
    const startIndex = jest.fn().mockResolvedValue(done({}));
    await render(<IndexSection api={engine({ startIndex })} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Check' }));
    expect(await screen.findByText('Everything matches.')).toBeTruthy();
    expect(startIndex).toHaveBeenCalledWith('verify');
    expect(screen.getByRole('button', { name: 'Repair' }).props.accessibilityState.disabled).toBe(true);
  });

  it('names what is wrong in words and offers the repair', async () => {
    const startIndex = jest.fn().mockResolvedValue(
      done({
        findings: [
          { kind: 'missing_from_index', meetingId: 'm1' },
          { kind: 'leftover_graph', meetingId: 'm2' },
        ],
      }),
    );
    await render(<IndexSection api={engine({ startIndex })} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Check' }));
    expect(await screen.findByText('2 differences found.')).toBeTruthy();
    expect(screen.getByText('Not searchable yet')).toBeTruthy();
    expect(screen.getByText('The graph holds a deleted recording')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Repair' }).props.accessibilityState.disabled).toBe(false);
  });

  it('follows a repair that is still running until it finishes', async () => {
    const startIndex = jest.fn().mockResolvedValue(done({ state: 'running', mode: 'repair' }));
    const getIndex = jest.fn().mockResolvedValue(done({ mode: 'repair', repaired: 2 }));
    jest.useFakeTimers();
    try {
      await render(<IndexSection api={engine({ startIndex, getIndex })} />);
      await fireEvent.press(screen.getByRole('button', { name: 'Repair' }));
      expect(await screen.findByText('Working')).toBeTruthy();
      jest.advanceTimersByTime(2100);
      await waitFor(() => expect(screen.getByText('Repaired: 2')).toBeTruthy());
    } finally {
      jest.useRealTimers();
    }
  });

  it('lists a meeting that could not be repaired, with the reason', async () => {
    const startIndex = jest
      .fn()
      .mockResolvedValue(done({ mode: 'repair', failed: [{ meetingId: 'm9', reason: 'ConnectionError' }] }));
    await render(<IndexSection api={engine({ startIndex })} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Repair' }));
    expect(await screen.findByText('Meeting m9 could not be repaired (ConnectionError).')).toBeTruthy();
  });

  it('says so when the engine does not answer', async () => {
    const startIndex = jest.fn().mockRejectedValue(new Error('down'));
    await render(<IndexSection api={engine({ startIndex })} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Check' }));
    expect(await screen.findByText('The engine did not answer. Try again.')).toBeTruthy();
  });
});
