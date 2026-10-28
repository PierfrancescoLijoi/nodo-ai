import { LOCALES } from '@/i18n';

import { createMockRepository } from '../mock/mockRepository';

describe.each(LOCALES.map((l) => l.code))('mock review inbox (%s)', (locale) => {
  test('proposals wait until decided and then leave the inbox', async () => {
    const repository = createMockRepository(locale);
    const waiting = await repository.listReviews();
    expect(waiting.length).toBeGreaterThanOrEqual(3);
    expect(waiting.every((item) => item.from.label && item.to.label && item.confidence > 0)).toBe(true);

    const [first, second] = waiting;
    await repository.reviewRelation(first!.id, 'approved');
    await repository.reviewRelation(second!.id, 'rejected');
    const left = await repository.listReviews();
    expect(left.map((item) => item.id)).toEqual(waiting.slice(2).map((item) => item.id));

    const graph = await repository.getGraph();
    expect(graph.edges.find((edge) => edge.id === first!.id)?.status).toBe('approved');
    expect(graph.edges.some((edge) => edge.id === second!.id)).toBe(false);
  });

  test('an unknown proposal is refused', async () => {
    await expect(createMockRepository(locale).reviewRelation('nope', 'approved')).rejects.toMatchObject({
      status: 404,
    });
  });
});
