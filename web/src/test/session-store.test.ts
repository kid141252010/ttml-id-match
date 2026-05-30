import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MockIdMatchClient } from '@/api/client';
import { useSessionStore } from '@/stores/session';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, client: new actual.MockIdMatchClient() };
});

describe('session workflow store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
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
});
