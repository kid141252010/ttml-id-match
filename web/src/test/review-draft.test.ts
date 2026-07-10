import { describe, expect, it } from 'vitest';

import {
  createReviewDraft,
  selectionCount,
  selectionsPayload,
  toggleDraftCandidate,
} from '@/domain/reviewDraft';
import type { PairPreview } from '@/api/types';

describe('ReviewDraft', () => {
  it('starts from each pair default and preserves dynamic source keys in apply order', () => {
    const previews = [
      preview('pair-b', { bandcamp: ['bc-1'], spotify: ['sp-1', 'sp-2'] }),
      preview('pair-a', { qq_music: ['qq-1'] }),
    ];

    const draft = createReviewDraft(previews);

    expect(selectionCount(draft)).toBe(4);
    expect(selectionsPayload(draft, previews)).toEqual([
      { pair_id: 'pair-b', sources: { bandcamp: ['bc-1'], spotify: ['sp-1', 'sp-2'] } },
      { pair_id: 'pair-a', sources: { qq_music: ['qq-1'] } },
    ]);
  });

  it('toggles any registered source without mutating the previous draft', () => {
    const previews = [preview('pair-1', { apple_music: ['apple-1'] })];
    const initial = createReviewDraft(previews);

    const added = toggleDraftCandidate(initial, 'pair-1', 'future_source', 'future-1');
    const removed = toggleDraftCandidate(added, 'pair-1', 'apple_music', 'apple-1');

    expect(initial.selections['pair-1'].sources).toEqual({ apple_music: ['apple-1'] });
    expect(removed.selections['pair-1'].sources).toEqual({
      apple_music: [],
      future_source: ['future-1'],
    });
    expect(selectionCount(removed)).toBe(1);
  });
});

function preview(pairId: string, sources: Record<string, string[]>): PairPreview {
  return {
    pair_id: pairId,
    files: { ttml: { filename: `${pairId}.ttml`, sha256: `sha-${pairId}` }, audio: null },
    sources: {},
    default_selection: { pair_id: pairId, sources },
    baseline_change_plan: {
      input_sha256: `sha-${pairId}`,
      output_sha256: `out-${pairId}`,
      final_text: `<tt>${pairId}</tt>`,
      changed: true,
      metadata: { added: {}, replaced: {}, skipped: {}, changed: false },
      normalization: {
        language_changed: false,
        body_text_changed: false,
        removed_translations: 0,
        removed_transliterations: 0,
        changed: false,
      },
    },
  };
}
