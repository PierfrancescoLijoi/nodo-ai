import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';

import { expect, LAUNCH, test } from './support/fixtures';

const TYPES = ['decision', 'person', 'project', 'topic', 'meeting', 'action', 'system'] as const;
const TYPE_NAMES = ['Aurora', 'Comet', 'Lantern', 'Nebula', 'Orbit', 'Quasar', 'Solstice'] as const;
const KINDS = [
  'ABOUT',
  'PROPOSED_BY',
  'DEPENDS_ON',
  'MENTIONED_IN',
  'ASSIGNED_TO',
  'DECIDED_IN',
  'SUPERSEDES',
] as const;

function fictionalGraph() {
  const nodes = Array.from({ length: 210 }, (_, index) => {
    const type = TYPES[index % TYPES.length];
    const serial = String(Math.floor(index / TYPES.length)).padStart(3, '0');
    return {
      id: `fictional-${type}-${serial}`,
      type,
      label: `Fictional ${TYPE_NAMES[index % TYPE_NAMES.length]} ${type} ${serial}`,
      ...(type === 'meeting' ? { meeting_id: `fictional-meeting-${serial}` } : {}),
    };
  });
  const edges = Array.from({ length: 378 }, (_, index) => {
    const source = nodes[index % nodes.length]!;
    const target = nodes[(index * 17 + 23) % nodes.length]!;
    return {
      id: `fictional-synapse-${index}`,
      source: source.id,
      target: source.id === target.id ? nodes[(index + 1) % nodes.length]!.id : target.id,
      kind: KINDS[index % KINDS.length],
      status: index % 5 === 0 ? 'proposed' : 'approved',
      confidence: 0.58 + (index % 40) / 100,
      ...(index % 11 === 0
        ? { evidence: [{ meeting_id: `fictional-meeting-${String(index % 30).padStart(3, '0')}` }] }
        : {}),
    };
  });
  return { nodes, edges, truncated: false };
}

const GRAPH = fictionalGraph();
const EMPTY_GRAPH = { nodes: [], edges: [], truncated: false };

async function mockBrain(page: Page, graph: typeof GRAPH | typeof EMPTY_GRAPH = GRAPH) {
  await page.route('**/v1/graph/subgraph**', (route) => route.fulfill({ json: graph }));
  await page.route('**/v1/graph/reviews**', (route) =>
    route.fulfill({ json: { nodes: [], edges: [], truncated: false } }),
  );
}

async function openBrain(page: Page) {
  await page.goto(LAUNCH);
  await page.getByRole('link', { name: 'Brain' }).first().click();
  await expect(page.getByRole('heading', { name: 'Brain', level: 1 })).toBeVisible();
  await expect(page.getByTestId('brain-canvas')).toBeVisible();
  await expect(page.getByTestId('brain-canvas')).toHaveAttribute('data-static-neurons', '0');
}

async function hoverAConcept(page: Page) {
  const canvas = page.getByTestId('brain-canvas');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('Brain canvas has no visible bounds');
  for (let y = box.y + box.height * 0.24; y < box.y + box.height * 0.78; y += 22) {
    for (let x = box.x + box.width * 0.22; x < box.x + box.width * 0.78; x += 22) {
      await page.mouse.move(x, y);
      if ((await page.getByTestId('brain-tooltip').count()) > 0) return;
    }
  }
  throw new Error('No rendered concept accepted hover input');
}

test('renders an immersive brain, focuses a concept and remains accessible', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  await mockBrain(page);
  await openBrain(page);
  await page.screenshot({ path: 'tmp-shots/brain-whole.png', fullPage: true });

  await hoverAConcept(page);
  await expect(page.getByTestId('brain-tooltip')).toBeVisible();
  await page.screenshot({ path: 'tmp-shots/brain-hover.png', fullPage: true });

  const search = page.getByRole('textbox', { name: 'Search the brain' });
  await search.fill('Fictional Aurora decision 000');
  await search.press('Enter');
  const panel = page.getByRole('complementary', { name: 'Node details' });
  await expect(panel).toContainText('Fictional Aurora decision 000');
  await expect(page.getByLabel('Current graph location')).toContainText('Fictional Aurora decision 000');
  await page.screenshot({ path: 'tmp-shots/brain-focused.png', fullPage: true });

  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
    .analyze();
  expect(result.violations.map((violation) => `${violation.id} (${violation.nodes.length})`)).toEqual([]);

  await page.keyboard.press('Escape');
  await page.getByRole('radio', { name: 'List' }).click();
  await expect(page.getByRole('button', { name: /Fictional Aurora decision 000/ }).first()).toBeVisible();
  await page.screenshot({ path: 'tmp-shots/brain-list.png', fullPage: true });
});

test('removes brain motion when reduced motion is requested', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockBrain(page);
  await openBrain(page);
  await expect(page.getByTestId('brain-canvas')).toHaveAttribute('data-motion', 'reduced');
  await page.screenshot({ path: 'tmp-shots/brain-reduced-motion.png', fullPage: true });
});

test('uses the list by default at phone width without horizontal overflow', async ({ page }) => {
  await mockBrain(page);
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(LAUNCH);
  await page.goto('/brain/');
  await expect(page.getByRole('radio', { name: 'List' })).toHaveAttribute('aria-checked', 'true');
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: 'tmp-shots/brain-phone.png', fullPage: true });
});

test('shows a polished empty brain state', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockBrain(page, EMPTY_GRAPH);
  await page.goto(LAUNCH);
  await page.goto('/brain/');
  await expect(page.getByRole('heading', { name: 'Your brain is ready to grow' })).toBeVisible();
  await page.screenshot({ path: 'tmp-shots/brain-empty.png', fullPage: true });
});
