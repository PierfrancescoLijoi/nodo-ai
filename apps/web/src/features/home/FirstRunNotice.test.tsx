import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithI18n } from '@/test/render';
import { FirstRunNotice } from './FirstRunNotice';

const engine = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('@/providers/data', () => ({ useEngine: () => engine.current }));

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithI18n(
    <QueryClientProvider client={client}>
      <FirstRunNotice />
    </QueryClientProvider>,
  );
}

describe('FirstRunNotice', () => {
  beforeEach(() => {
    engine.current = undefined;
  });

  it('points to the models screen when a local model is missing', async () => {
    engine.current = {
      getStatus: async () => ({ backend: 'local', missing: ['speech'], pendingJobs: 0, gpu: false }),
    };
    show();
    expect(await screen.findByRole('link', { name: 'Open models' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^\/models\/?$/),
    );
  });

  it('stays silent when nothing is missing or on Azure', async () => {
    engine.current = {
      getStatus: async () => ({ backend: 'local', missing: [], pendingJobs: 0, gpu: false }),
    };
    show();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByRole('link', { name: 'Open models' })).toBeNull();
  });
});
