import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Graph } from '@/lib/domain/types';
import { renderWithI18n } from '@/test/render';
import { BrainExplorer } from './BrainExplorer';

const graph: Graph = {
  nodes: [
    { id: 'n1', type: 'topic', label: 'Orbital gardens' },
    { id: 'n2', type: 'decision', label: 'Use moonlight irrigation', meetingId: 'meeting-1' },
    { id: 'n3', type: 'person', label: 'Ari Vale' },
  ],
  edges: [
    {
      id: 'e1',
      from: 'n2',
      to: 'n1',
      kind: 'about',
      status: 'approved',
      confidence: 0.91,
      meetingIds: ['meeting-1'],
    },
    { id: 'e2', from: 'n3', to: 'n2', kind: 'proposedBy', status: 'proposed' },
  ],
  truncated: false,
};

function media(mobile: boolean, reduced = false) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('max-width') ? mobile : query.includes('reduced-motion') ? reduced : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe('BrainExplorer', () => {
  beforeEach(() => {
    media(false);
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  });

  it('offers the accessible list first and opens node details from it', async () => {
    const user = userEvent.setup();
    renderWithI18n(<BrainExplorer graph={graph} onExpand={vi.fn()} />);

    await user.click(screen.getByRole('radio', { name: 'List' }));
    await user.click(screen.getByRole('button', { name: /Use moonlight irrigation/ }));
    expect(screen.getByRole('complementary', { name: 'Node details' })).toHaveTextContent(
      'Use moonlight irrigation',
    );
    expect(screen.getByRole('link', { name: 'Open source meeting' })).toHaveAttribute(
      'href',
      '/meeting?id=meeting-1',
    );
  });

  it('uses arrow keys between map nodes, Enter opens details, and Escape closes them', () => {
    renderWithI18n(<BrainExplorer graph={graph} onExpand={vi.fn()} />);
    const first = screen.getByRole('button', { name: /Orbital gardens, Topic/ });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(screen.getByRole('button', { name: /Use moonlight irrigation, Decision/ })).toHaveFocus();
    fireEvent.keyDown(document.activeElement as Element, { key: 'Enter' });
    expect(screen.getByRole('complementary', { name: 'Node details' })).toBeVisible();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('complementary', { name: 'Node details' })).not.toBeInTheDocument();
  });

  it('defaults to list view on a small screen', () => {
    media(true);
    renderWithI18n(<BrainExplorer graph={graph} onExpand={vi.fn()} />);
    expect(screen.getByRole('radio', { name: 'List' })).toHaveAttribute('aria-checked', 'true');
  });

  it('disables ambient motion when reduced motion is requested', () => {
    media(false, true);
    renderWithI18n(<BrainExplorer graph={graph} onExpand={vi.fn()} />);
    expect(screen.getByTestId('brain-canvas')).toHaveAttribute('data-motion', 'reduced');
  });

  it('offers an immersive fullscreen control for the map', () => {
    renderWithI18n(<BrainExplorer graph={graph} onExpand={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toBeVisible();
    expect(screen.getByTestId('brain-map-shell')).toHaveAttribute('data-immersive', 'true');
  });
});
