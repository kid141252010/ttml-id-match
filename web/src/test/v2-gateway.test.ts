import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpIdMatchGateway } from '@/api/client';
import type { SelectionPayload } from '@/api/types';

describe('v2 HTTP gateway', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses only the v2 session workflow endpoints and wire payloads', async () => {
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const responses = [
      { session_id: 'session-1', session_token: 'token-1', expires_at: '2026-07-14T00:00:00Z' },
      undefined,
      { pairs: [], issues: [] },
      { job_id: 'job-1', status: 'pending', total: 1, completed: 0, results: [], pair_failures: [], errors: [], snapshot_id: null },
      { job_id: 'job-1', status: 'running', total: 1, completed: 0, results: [], pair_failures: [], errors: [], snapshot_id: null },
      { job_id: 'job-1', status: 'completed', total: 1, completed: 1, results: [], pair_failures: [], errors: [], snapshot_id: 'snapshot-1' },
      changePlan(),
      applySummary(),
      'single-file',
      'zip-file',
    ];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      requests.push({ url, init });
      const body = responses.shift();
      return new Response(body === undefined ? null : JSON.stringify(body), {
        status: body === undefined ? 204 : 200,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      });
    }));

    const gateway = new HttpIdMatchGateway('/api/v2');
    const selection: SelectionPayload = { pair_id: 'pair-1', sources: { qq_music: ['qq-1'] } };

    await gateway.createSession();
    await gateway.deleteSession('session-1', 'token-1');
    await gateway.uploadFiles('session-1', 'token-1', [new File(['ttml'], 'Song.ttml')]);
    await gateway.createPreviewJob('session-1', 'token-1');
    await gateway.getPreviewJob('session-1', 'token-1', 'job-1');
    await gateway.stepPreviewJob('session-1', 'token-1', 'job-1');
    await gateway.changePlan('session-1', 'token-1', 'snapshot-1', selection);
    await gateway.apply('session-1', 'token-1', 'snapshot-1', [selection]);
    await gateway.downloadFile('session-1', 'token-1', 'Song #1.ttml');
    await gateway.downloadAll('session-1', 'token-1');

    expect(requests.map(({ url, init }) => `${init.method ?? 'GET'} ${url}`)).toEqual([
      'POST /api/v2/sessions',
      'DELETE /api/v2/sessions/session-1',
      'POST /api/v2/sessions/session-1/files',
      'POST /api/v2/sessions/session-1/preview-jobs',
      'GET /api/v2/sessions/session-1/preview-jobs/job-1',
      'POST /api/v2/sessions/session-1/preview-jobs/job-1/steps',
      'POST /api/v2/sessions/session-1/change-plans',
      'POST /api/v2/sessions/session-1/apply',
      'GET /api/v2/sessions/session-1/outputs/Song%20%231.ttml',
      'GET /api/v2/sessions/session-1/outputs.zip',
    ]);
    expect(JSON.parse(requests[6].init.body as string)).toEqual({ snapshot_id: 'snapshot-1', selection });
    expect(JSON.parse(requests[7].init.body as string)).toEqual({ snapshot_id: 'snapshot-1', selections: [selection] });
    for (const request of requests.slice(1)) {
      expect(new Headers(request.init.headers).get('Authorization')).toBe('Bearer token-1');
    }
  });

  it('reads the unified v2 error body without a FastAPI detail wrapper', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      code: 'snapshot_conflict',
      message: 'snapshot expired',
      retryable: false,
      details: { snapshot_id: 'old' },
    }), { status: 409, headers: { 'Content-Type': 'application/json' } })));

    const gateway = new HttpIdMatchGateway('/api/v2');
    await expect(gateway.changePlan(
      'session-1',
      'token-1',
      'old',
      { pair_id: 'pair-1', sources: {} },
    )).rejects.toMatchObject({
      status: 409,
      code: 'snapshot_conflict',
      message: 'snapshot expired',
      details: { snapshot_id: 'old' },
    });
  });
});

function changePlan() {
  return {
    snapshot_id: 'snapshot-1',
    pair_id: 'pair-1',
    input_sha256: 'before',
    output_sha256: 'after',
    final_text: '<tt>after</tt>',
    changed: true,
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

function applySummary() {
  return { snapshot_id: 'snapshot-1', succeeded: 0, failed: 0, skipped: 0, files: [] };
}
