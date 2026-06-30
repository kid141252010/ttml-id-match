import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MockIdMatchClient } from '@/api/client';
import { useSessionStore } from '@/stores/session';
import type { FilePair, PreviewResult } from '@/api/types';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, client: new actual.MockIdMatchClient() };
});

describe('session workflow store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useSessionStore().setClient(new MockIdMatchClient());
  });

  it('creates a session, pairs uploaded files, previews changes, and applies selections', async () => {
    const store = useSessionStore();

    await store.ensureSession();
    expect(store.sessionId).toMatch(/^mock-/);

    await store.uploadFiles([
      new File(['ttml'], 'Disease.ttml', { type: 'application/xml' }),
      new File(['audio'], 'Disease.flac', { type: 'audio/flac' }),
      new File(['ttml'], 'Lyrics Only.ttml', { type: 'application/xml' }),
    ]);

    expect(store.pairs).toHaveLength(2);
    expect(store.pairs[0]).toMatchObject({ ttml: 'Disease.ttml', audio: 'Disease.flac', status: 'paired' });
    expect(store.pairs[1]).toMatchObject({ ttml: 'Lyrics Only.ttml', audio: null, status: 'ttml_only' });
    expect(store.currentStep).toBe('upload');

    await store.previewAll();
    expect(store.currentStep).toBe('preview');
    expect(store.previewResults[0].changes.added.musicName).toContain('Disease');
    expect(store.previewResults[0].apple_music.candidates_by_storefront.cn).toHaveLength(1);
    expect(store.selectionCount).toBeGreaterThan(0);

    await store.applySelections();
    expect(store.currentStep).toBe('result');
    expect(store.resultSummary).toMatchObject({ succeeded: 2, failed: 0, skipped: 0 });
  });

  it('uses preview job steps so partial results can arrive before completion', async () => {
    const store = useSessionStore();
    const client = new MockIdMatchClient();
    const calls: string[] = [];
    vi.spyOn(client, 'createSession').mockResolvedValue({ session_id: 'mock-job' });
    vi.spyOn(client, 'uploadFiles').mockResolvedValue({
      files: [
        { name: 'A.ttml', size: 1, kind: 'ttml' },
        { name: 'B.ttml', size: 1, kind: 'ttml' },
      ],
      pairs: [
        { id: 'pair-1', ttml: 'A.ttml', audio: null, status: 'ttml_only' },
        { id: 'pair-2', ttml: 'B.ttml', audio: null, status: 'ttml_only' },
      ],
    });
    vi.spyOn(client, 'createPreviewJob').mockImplementation(async (_sessionId: string, pairs: FilePair[]) => {
      calls.push('create');
      return { job_id: 'job-1', status: 'pending', total: pairs.length, completed: 0, results: [] };
    });
    vi.spyOn(client, 'stepPreviewJob')
      .mockImplementationOnce(async () => {
        calls.push('step-1');
        return { job_id: 'job-1', status: 'running', total: 2, completed: 1, results: [preview('pair-1', 'A.ttml')] };
      })
      .mockImplementationOnce(async () => {
        calls.push('step-2');
        return {
          job_id: 'job-1',
          status: 'complete',
          total: 2,
          completed: 2,
          results: [preview('pair-1', 'A.ttml'), preview('pair-2', 'B.ttml')],
        };
      });
    store.setClient(client);

    await store.uploadFiles([new File(['a'], 'A.ttml'), new File(['b'], 'B.ttml')]);
    await store.previewAll();

    expect(calls).toEqual(['create', 'step-1', 'step-2']);
    expect(store.currentStep).toBe('preview');
    expect(store.previewResults.map((result) => result.ttml)).toEqual(['A.ttml', 'B.ttml']);
    expect(store.progressEvents[0].message).toContain('预览完成 2/2');
  });
});

function preview(pairId: string, ttml: string): PreviewResult {
  return {
    pair_id: pairId,
    ttml,
    audio: null,
    apple_music: sourcePreview(),
    qq_music: sourcePreview(),
    ncm_music: sourcePreview(),
    spotify: sourcePreview(),
    changes: {
      added: { musicName: [ttml.replace('.ttml', '')] },
      replaced: {},
      skipped: {},
    },
  };
}

function sourcePreview(): PreviewResult['apple_music'] {
  return {
    best: [],
    candidates: [],
    candidates_by_storefront: {},
    candidates_by_market: {},
    errors: [],
  };
}
