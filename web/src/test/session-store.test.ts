import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createSessionStore } from '@/stores/session';
import { ApiError } from '@/api/client';
import type { IdMatchGateway } from '@/api/client';
import type {
  ApplySummary,
  ChangePlanResponse,
  PairPreview,
  PreviewJobResponse,
} from '@/api/types';

describe('v2 session workflow store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('stages partial preview results and commits them only with the completed snapshot', async () => {
    const finalStep = deferred<PreviewJobResponse>();
    const previews = [preview('pair-1', 'A.ttml'), preview('pair-2', 'B.ttml')];
    const fake = fakeGateway({
      stepPreviewJob: vi.fn()
        .mockResolvedValueOnce(job('running', [previews[0]], null))
        .mockImplementationOnce(() => finalStep.promise),
    });
    const useStore = createSessionStore(fake, 'session-staging');
    const store = useStore();

    await store.uploadFiles([new File(['a'], 'A.ttml'), new File(['b'], 'B.ttml')]);
    const pending = store.previewAll();

    await vi.waitFor(() => expect(store.previewStaging).toHaveLength(1));
    expect(store.operation).toBe('previewing');
    expect(store.previewResults).toEqual([]);
    expect(store.snapshotId).toBeNull();

    finalStep.resolve(job('completed', previews, 'snapshot-1'));
    await pending;

    expect(store.previewStaging).toEqual([]);
    expect(store.previewResults.map((item) => item.pair_id)).toEqual(['pair-1', 'pair-2']);
    expect(store.snapshotId).toBe('snapshot-1');
    expect(store.selections['pair-1']).toEqual({ pair_id: 'pair-1', sources: { qq_music: ['pair-1-qq'] } });
    expect(store.selectedChangePlan?.snapshot_id).toBe('snapshot-1');
    expect(store.selectionCount).toBe(2);
    expect(store.canApply).toBe(true);
    expect(store.operation).toBe('idle');
  });

  it('keeps the previous complete review when a re-preview fails after partial results', async () => {
    const oldPreviews = [preview('pair-1', 'A.ttml'), preview('pair-2', 'B.ttml')];
    const partialReplacement = preview('pair-1', 'Changed.ttml');
    const fake = fakeGateway({
      stepPreviewJob: vi.fn()
        .mockResolvedValueOnce(job('completed', oldPreviews, 'snapshot-old'))
        .mockResolvedValueOnce({
          ...job('failed', [partialReplacement], null),
          errors: [{ code: 'source_failed', message: 'preview stopped', retryable: true, details: {} }],
        }),
    });
    const store = createSessionStore(fake, 'session-preview-failure')();
    await store.uploadFiles([new File(['a'], 'A.ttml'), new File(['b'], 'B.ttml')]);
    await store.previewAll();

    await expect(store.previewAll()).rejects.toThrow('preview stopped');

    expect(store.previewResults.map((item) => item.files.ttml.filename)).toEqual(['A.ttml', 'B.ttml']);
    expect(store.snapshotId).toBe('snapshot-old');
    expect(store.selections['pair-1'].sources).toEqual({ qq_music: ['pair-1-qq'] });
    expect(store.previewStaging).toEqual([]);
    expect(store.canApply).toBe(true);
  });

  it('does not show another pair preview when the selected pair failed', async () => {
    const failedJob = job(
      'completed_with_errors',
      [preview('pair-1', 'A.ttml')],
      'snapshot-1',
    );
    failedJob.pair_failures = [{
      pair_id: 'pair-2',
      ttml_path: 'B.ttml',
      audio_path: null,
      error: {
        code: 'pair_preview_failed',
        message: 'invalid TTML',
        retryable: false,
        details: { pair_id: 'pair-2' },
      },
    }];
    const fake = fakeGateway({
      stepPreviewJob: vi.fn().mockResolvedValue(failedJob),
    });
    const store = createSessionStore(fake, 'session-failed-pair-selection')();
    await store.uploadFiles([new File(['a'], 'A.ttml'), new File(['b'], 'B.ttml')]);
    await store.previewAll();

    store.selectPair('pair-2');

    expect(store.selectedPreview).toBeNull();
    expect(store.selectedChangePlan).toBeNull();
    expect(store.selectedPreviewFailure?.error.message).toBe('invalid TTML');
    expect(store.failedPairIds).toEqual(['pair-2']);
    expect(store.canApply).toBe(false);
  });

  it('keeps an all-failed preview visible for diagnosis', async () => {
    const allFailed = job('completed_with_errors', [], 'snapshot-failed');
    allFailed.pair_failures = [{
      pair_id: 'pair-1',
      ttml_path: 'A.ttml',
      audio_path: null,
      error: {
        code: 'pair_preview_failed',
        message: 'malformed XML',
        retryable: false,
        details: {},
      },
    }];
    allFailed.completed = 1;
    allFailed.total = 1;
    const store = createSessionStore(fakeGateway({
      stepPreviewJob: vi.fn().mockResolvedValue(allFailed),
    }), 'session-all-failed')();
    await store.uploadFiles([new File(['broken'], 'A.ttml')]);

    await store.previewAll();

    expect(store.hasPreviewOutcomes).toBe(true);
    expect(store.previewResults).toEqual([]);
    expect(store.selectedPreviewFailure?.ttml_path).toBe('A.ttml');
    expect(store.canApply).toBe(false);
  });

  it('ignores an out-of-order change plan response and enables apply for the latest selection', async () => {
    const firstPlan = deferred<ChangePlanResponse>();
    const secondPlan = deferred<ChangePlanResponse>();
    const changePlanRequest = vi.fn()
      .mockImplementationOnce(() => firstPlan.promise)
      .mockImplementationOnce(() => secondPlan.promise);
    const fake = fakeGateway({
      stepPreviewJob: vi.fn().mockResolvedValue(job('completed', [preview('pair-1', 'A.ttml')], 'snapshot-1')),
      changePlan: changePlanRequest,
    });
    const store = createSessionStore(fake, 'session-change-plan-order')();
    await store.uploadFiles([new File(['a'], 'A.ttml')]);
    await store.previewAll();

    store.toggleCandidate('pair-1', 'qq_music', 'pair-1-qq');
    const firstFlush = store.flushChangePlan('pair-1');
    await vi.waitFor(() => expect(changePlanRequest).toHaveBeenCalledTimes(1));
    expect(store.canApply).toBe(false);

    store.toggleCandidate('pair-1', 'qq_music', 'pair-1-qq');
    const secondFlush = store.flushChangePlan('pair-1');
    await vi.waitFor(() => expect(changePlanRequest).toHaveBeenCalledTimes(2));
    secondPlan.resolve(changePlan('pair-1', 'snapshot-1', 'latest-output'));
    await secondFlush;

    expect(store.selectedChangePlan?.output_sha256).toBe('latest-output');
    expect(store.canApply).toBe(true);
    expect(changePlanRequest.mock.calls[1][3]).toEqual({
      pair_id: 'pair-1',
      sources: { qq_music: ['pair-1-qq'] },
    });

    firstPlan.resolve(changePlan('pair-1', 'snapshot-1', 'stale-output'));
    await firstFlush;
    expect(store.selectedChangePlan?.output_sha256).toBe('latest-output');
  });

  it('deletes the remote session before clearing all local workflow state', async () => {
    const deleteSession = vi.fn().mockResolvedValue(undefined);
    const fake = fakeGateway({ deleteSession });
    const store = createSessionStore(fake, 'session-reset')();
    await store.uploadFiles([new File(['a'], 'A.ttml')]);

    await store.resetSession();

    expect(deleteSession).toHaveBeenCalledWith('session-1', 'token-1');
    expect(store.sessionId).toBeNull();
    expect(store.files).toEqual([]);
    expect(store.pairs).toEqual([]);
    expect(store.previewResults).toEqual([]);
    expect(store.selections).toEqual({});
    expect(store.resultSummary).toBeNull();
    expect(store.operation).toBe('idle');
  });

  it('invalidates the local review when apply reports a stale snapshot', async () => {
    const fake = fakeGateway({
      stepPreviewJob: vi.fn().mockResolvedValue(job('completed', [preview('pair-1', 'A.ttml')], 'snapshot-1')),
      apply: vi.fn().mockRejectedValue(
        new ApiError('snapshot expired', 409, 'snapshot_conflict'),
      ),
    });
    const store = createSessionStore(fake, 'session-stale-snapshot')();
    await store.uploadFiles([new File(['a'], 'A.ttml')]);
    await store.previewAll();

    await expect(store.applySelections()).rejects.toThrow('snapshot expired');

    expect(store.snapshotId).toBeNull();
    expect(store.changePlans).toEqual({});
    expect(store.canApply).toBe(false);
    expect(store.operation).toBe('idle');
  });
});

function fakeGateway(overrides: Partial<IdMatchGateway> = {}): IdMatchGateway {
  return {
    createSession: vi.fn().mockResolvedValue({
      session_id: 'session-1',
      session_token: 'token-1',
      expires_at: '2026-07-14T00:00:00Z',
    }),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    uploadFiles: vi.fn().mockResolvedValue({
      pairs: [
        { pair_id: 'pair-1', status: 'ttml_only', ttml_path: 'A.ttml', audio_path: null, audio_candidates: [] },
        { pair_id: 'pair-2', status: 'ttml_only', ttml_path: 'B.ttml', audio_path: null, audio_candidates: [] },
      ],
      issues: [],
    }),
    createPreviewJob: vi.fn().mockResolvedValue(job('pending', [], null)),
    getPreviewJob: vi.fn().mockResolvedValue(job('pending', [], null)),
    stepPreviewJob: vi.fn().mockResolvedValue(job('completed', [], 'snapshot-empty')),
    changePlan: vi.fn().mockResolvedValue(changePlan('pair-1', 'snapshot-1', 'out')),
    apply: vi.fn().mockResolvedValue(applySummary()),
    downloadAll: vi.fn().mockResolvedValue(new Blob()),
    downloadFile: vi.fn().mockResolvedValue(new Blob()),
    ...overrides,
  };
}

function preview(pairId: string, filename: string): PairPreview {
  return {
    pair_id: pairId,
    files: { ttml: { filename, sha256: `sha-${pairId}` }, audio: null },
    sources: {
      qq_music: {
        source: 'qq_music',
        candidates: [
          {
            id: `${pairId}-qq`,
            source: 'qq_music',
            title: filename.replace('.ttml', ''),
            artists: ['Artist'],
            album: null,
            aliases: [],
            identifiers: {},
            group: null,
            rank: 1,
            recommended: true,
            evidence: [],
            duration_ms: null,
            release_date: null,
          },
        ],
        groups: {},
        recommended_ids: [`${pairId}-qq`],
        warnings: [],
      },
    },
    default_selection: { pair_id: pairId, sources: { qq_music: [`${pairId}-qq`] } },
    baseline_change_plan: summary(`sha-${pairId}`, `out-${pairId}`),
  };
}

function job(
  status: PreviewJobResponse['status'],
  results: PairPreview[],
  snapshotId: string | null,
): PreviewJobResponse {
  return {
    job_id: 'job-1',
    status,
    total: 2,
    completed: results.length,
    results,
    pair_failures: [],
    errors: [],
    snapshot_id: snapshotId,
  };
}

function summary(input: string, output: string) {
  return {
    input_sha256: input,
    output_sha256: output,
    final_text: `<tt>${output}</tt>`,
    changed: input !== output,
    metadata: { added: {}, replaced: {}, skipped: {}, changed: false },
    normalization: {
      language_changed: false,
      body_text_changed: false,
      removed_translations: 0,
      removed_transliterations: 0,
      changed: false,
    },
  };
}

function changePlan(pairId: string, snapshotId: string, output: string): ChangePlanResponse {
  return { snapshot_id: snapshotId, pair_id: pairId, ...summary(`sha-${pairId}`, output) };
}

function applySummary(): ApplySummary {
  return { snapshot_id: 'snapshot-1', succeeded: 0, failed: 0, skipped: 0, files: [] };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}
