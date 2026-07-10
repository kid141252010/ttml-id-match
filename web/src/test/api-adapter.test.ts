import { describe, expect, it } from 'vitest';

import { adaptPreviewJob } from '@/api/adapter';


describe('OpenAPI DTO adapter', () => {
  it('normalizes optional wire fields and preserves a newly added source key', () => {
    const result = adaptPreviewJob({
      job_id: 'job-1',
      status: 'completed',
      total: 1,
      completed: 1,
      snapshot_id: 'snapshot-1',
      results: [{
        pair_id: 'pair-1',
        files: { ttml: { filename: 'Song.ttml', sha256: 'sha' } },
        sources: {
          bandcamp: {
            source: 'bandcamp',
            candidates: [{
              id: 'bc-1', source: 'bandcamp', rank: 1, recommended: true,
            }],
          },
        },
        default_selection: { pair_id: 'pair-1', sources: { bandcamp: ['bc-1'] } },
        baseline_change_plan: {
          input_sha256: 'sha', output_sha256: 'out', final_text: '<tt>out</tt>', changed: true,
          metadata: { changed: false },
          normalization: {
            language_changed: false, body_text_changed: false,
            removed_translations: 0, removed_transliterations: 0, changed: false,
          },
        },
      }],
    });

    const candidate = result.results[0].sources.bandcamp.candidates[0];
    expect(candidate.artists).toEqual([]);
    expect(candidate.identifiers).toEqual({});
    expect(result.results[0].default_selection.sources.bandcamp).toEqual(['bc-1']);
  });
});
