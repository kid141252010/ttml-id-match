import { defineStore } from 'pinia';

import { ApiError, gateway as defaultGateway } from '@/api/client';
import type { IdMatchGateway } from '@/api/client';
import type {
  ApplySummary,
  ChangePlanResponse,
  FilePair,
  OperationState,
  PairPreview,
  PairPreviewFailure,
  PairingIssue,
  PreviewJobResponse,
  ProgressEvent,
  SelectionPayload,
  SessionFile,
  SourceKey,
} from '@/api/types';
import {
  createReviewDraft,
  selectionCount as countSelections,
  selectionsPayload,
  toggleDraftCandidate,
} from '@/domain/reviewDraft';

const AUDIO_EXTENSIONS = new Set([
  'aac', 'aif', 'aiff', 'alac', 'ape', 'flac', 'm4a', 'm4b', 'm4p', 'mp3',
  'mp4', 'ogg', 'opus', 'wav', 'wma',
]);
const CHANGE_PLAN_DEBOUNCE_MS = 120;

interface SessionState {
  sessionId: string | null;
  sessionToken: string | null;
  expiresAt: string | null;
  files: SessionFile[];
  pairs: FilePair[];
  pairingIssues: PairingIssue[];
  previewResults: PairPreview[];
  previewStaging: PairPreview[];
  previewFailures: PairPreviewFailure[];
  previewFailureStaging: PairPreviewFailure[];
  snapshotId: string | null;
  selections: Record<string, SelectionPayload>;
  changePlans: Record<string, ChangePlanResponse>;
  baselineChangePlans: Record<string, ChangePlanResponse>;
  changePlanPending: Record<string, boolean>;
  changePlanRevisions: Record<string, number>;
  resultSummary: ApplySummary | null;
  selectedPairId: string | null;
  operation: OperationState;
  progressEvents: ProgressEvent[];
}

export function createSessionStore(gateway: IdMatchGateway, storeId = 'session') {
  const debounceTimers = new Map<string, ReturnType<typeof setTimeout>>();

  return defineStore(storeId, {
    state: (): SessionState => initialState(),

    getters: {
      selectedPreview(state): PairPreview | null {
        if (state.selectedPairId) {
          return state.previewResults.find((result) => result.pair_id === state.selectedPairId) ?? null;
        }
        return state.previewResults[0] ?? null;
      },
      selectedChangePlan(state): ChangePlanResponse | null {
        const pairId = state.selectedPairId ?? state.previewResults[0]?.pair_id;
        return pairId ? state.changePlans[pairId] ?? null : null;
      },
      selectedPreviewFailure(state): PairPreviewFailure | null {
        if (!state.selectedPairId) return state.previewFailures[0] ?? null;
        return state.previewFailures.find((failure) => failure.pair_id === state.selectedPairId) ?? null;
      },
      failedPairIds(state): string[] {
        return state.previewFailures.map((failure) => failure.pair_id);
      },
      hasPreviewOutcomes(state): boolean {
        return state.previewResults.length + state.previewFailures.length > 0;
      },
      selectionCount(state): number {
        return countSelections({ selections: state.selections });
      },
      canPreview(state): boolean {
        return state.pairs.length > 0 && state.pairingIssues.length === 0;
      },
      canApply(state): boolean {
        if (!state.snapshotId || state.previewResults.length === 0 || state.previewFailures.length > 0 || state.operation !== 'idle') return false;
        if (Object.values(state.changePlanPending).some(Boolean)) return false;
        return state.previewResults.every((preview) => (
          Boolean(state.selections[preview.pair_id]) && Boolean(state.changePlans[preview.pair_id])
        ));
      },
      loading(state): boolean {
        return state.operation !== 'idle';
      },
    },

    actions: {
      async ensureSession(): Promise<void> {
        if (this.sessionId && this.sessionToken) return;
        const response = await gateway.createSession();
        this.sessionId = response.session_id;
        this.sessionToken = response.session_token;
        this.expiresAt = response.expires_at;
        this.addProgress('info', `Session created ${response.session_id}`);
      },

      async uploadFiles(files: File[]): Promise<void> {
        this.operation = 'uploading';
        try {
          await this.ensureSession();
          const response = await gateway.uploadFiles(this.sessionId!, this.sessionToken!, files);
          this.cancelChangePlanRequests();
          this.files = files.map(toSessionFile);
          this.pairs = response.pairs.map((pair) => ({
            id: pair.pair_id,
            ttml: pair.ttml_path,
            audio: pair.audio_path,
            status: pair.status,
            audio_candidates: [...pair.audio_candidates],
          }));
          this.pairingIssues = response.issues;
          this.previewResults = [];
          this.previewStaging = [];
          this.previewFailures = [];
          this.previewFailureStaging = [];
          this.snapshotId = null;
          this.selections = {};
          this.changePlans = {};
          this.baselineChangePlans = {};
          this.resultSummary = null;
          this.selectedPairId = this.pairs[0]?.id ?? null;
          this.addProgress('success', `Recognized ${this.pairs.length} TTML pairs`);
        } catch (error) {
          this.addProgress('error', errorMessage(error));
          throw error;
        } finally {
          this.operation = 'idle';
        }
      },

      async previewAll(): Promise<void> {
        await this.ensureSession();
        this.operation = 'previewing';
        this.previewStaging = [];
        try {
          let job = await gateway.createPreviewJob(this.sessionId!, this.sessionToken!);
          this.stagePreviewJob(job);
          while (job.status === 'pending' || job.status === 'running') {
            job = await gateway.stepPreviewJob(this.sessionId!, this.sessionToken!, job.job_id);
            this.stagePreviewJob(job);
          }
          if (job.status === 'failed') throw new Error(previewJobError(job));
          if (!job.snapshot_id) throw new Error('Completed preview job did not publish a snapshot');

          this.commitPreviewJob(job);
          const pairFailures = job.pair_failures ?? [];
          if (pairFailures.length > 0) {
            this.addProgress('warning', `Preview completed with ${pairFailures.length} failed pair(s)`);
          } else {
            const suffix = job.status === 'completed_with_errors' ? ' with source errors' : '';
            this.addProgress('success', `Preview completed ${job.completed}/${job.total}${suffix}`);
          }
        } catch (error) {
          this.previewStaging = [];
          this.previewFailureStaging = [];
          this.addProgress('error', errorMessage(error));
          throw error;
        } finally {
          this.operation = 'idle';
        }
      },

      toggleCandidate(pairId: string, source: SourceKey, candidateId: string): void {
        const next = toggleDraftCandidate({ selections: this.selections }, pairId, source, candidateId);
        this.selections = next.selections;
        delete this.changePlans[pairId];
        this.queueChangePlan(pairId);
      },

      acceptBestForAll(): void {
        this.cancelChangePlanRequests();
        this.selections = createReviewDraft(this.previewResults).selections;
        this.changePlans = clonePlanMap(this.baselineChangePlans);
        this.addProgress('info', 'Accepted all recommended candidates');
      },

      async flushChangePlan(pairId?: string): Promise<void> {
        const pairIds = pairId
          ? [pairId]
          : Object.keys(this.changePlanPending).filter((id) => this.changePlanPending[id]);
        await Promise.all(pairIds.map(async (id) => {
          const timer = debounceTimers.get(id);
          if (timer) {
            clearTimeout(timer);
            debounceTimers.delete(id);
          }
          const revision = this.changePlanRevisions[id];
          if (revision !== undefined && this.changePlanPending[id]) {
            await this.requestChangePlan(id, revision);
          }
        }));
      },

      async applySelections(): Promise<void> {
        await this.flushChangePlan();
        await this.ensureSession();
        if (!this.snapshotId) throw new Error('Preview snapshot is required before apply');
        const payload = selectionsPayload({ selections: this.selections }, this.previewResults);
        if (payload.length !== this.previewResults.length || !this.previewResults.every((item) => this.changePlans[item.pair_id])) {
          throw new Error('Every preview pair requires a current selection plan');
        }

        this.operation = 'applying';
        try {
          this.resultSummary = await gateway.apply(
            this.sessionId!,
            this.sessionToken!,
            this.snapshotId,
            payload,
          );
          this.addProgress('success', 'Apply completed');
        } catch (error) {
          this.invalidateSnapshot(error);
          this.addProgress('error', errorMessage(error));
          throw error;
        } finally {
          this.operation = 'idle';
        }
      },

      async resetSession(): Promise<void> {
        const sessionId = this.sessionId;
        if (sessionId) await gateway.deleteSession(sessionId, this.sessionToken ?? '');
        this.cancelChangePlanRequests();
        Object.assign(this, initialState());
      },

      selectPair(pairId: string): void {
        this.selectedPairId = pairId;
      },

      addProgress(level: ProgressEvent['level'], message: string): void {
        this.progressEvents.unshift({
          id: `${Date.now()}-${this.progressEvents.length}`,
          level,
          message,
          at: new Date().toLocaleTimeString(),
        });
        this.progressEvents = this.progressEvents.slice(0, 8);
      },

      stagePreviewJob(job: PreviewJobResponse): void {
        this.previewStaging = [...job.results];
        this.previewFailureStaging = [...(job.pair_failures ?? [])];
        if (job.completed > 0 && (job.status === 'pending' || job.status === 'running')) {
          this.addProgress('info', `Preview progress ${job.completed}/${job.total}`);
        }
      },

      commitPreviewJob(job: PreviewJobResponse): void {
        const snapshotId = job.snapshot_id!;
        this.cancelChangePlanRequests();
        this.previewResults = [...job.results];
        this.previewStaging = [];
        this.previewFailures = [...(job.pair_failures ?? [])];
        this.previewFailureStaging = [];
        this.snapshotId = snapshotId;
        this.selections = createReviewDraft(job.results).selections;
        this.baselineChangePlans = Object.fromEntries(job.results.map((preview) => [
          preview.pair_id,
          {
            snapshot_id: snapshotId,
            pair_id: preview.pair_id,
            ...preview.baseline_change_plan,
          },
        ]));
        this.changePlans = clonePlanMap(this.baselineChangePlans);
        this.selectedPairId = this.pairs.find((pair) => (
          job.results.some((result) => result.pair_id === pair.id)
          || (job.pair_failures ?? []).some((failure) => failure.pair_id === pair.id)
        ))?.id ?? null;
        this.resultSummary = null;
      },

      async downloadAllOutputs(): Promise<Blob> {
        if (!this.sessionId || !this.sessionToken) throw new Error('Session credentials are required');
        return gateway.downloadAll(this.sessionId, this.sessionToken);
      },

      async downloadOutput(filename: string): Promise<Blob> {
        if (!this.sessionId || !this.sessionToken) throw new Error('Session credentials are required');
        return gateway.downloadFile(this.sessionId, this.sessionToken, filename);
      },

      queueChangePlan(pairId: string): void {
        const revision = (this.changePlanRevisions[pairId] ?? 0) + 1;
        this.changePlanRevisions[pairId] = revision;
        this.changePlanPending[pairId] = true;
        const previous = debounceTimers.get(pairId);
        if (previous) clearTimeout(previous);
        debounceTimers.set(pairId, setTimeout(() => {
          debounceTimers.delete(pairId);
          void this.requestChangePlan(pairId, revision).catch(() => undefined);
        }, CHANGE_PLAN_DEBOUNCE_MS));
      },

      async requestChangePlan(pairId: string, revision: number): Promise<void> {
        const sessionId = this.sessionId;
        const snapshotId = this.snapshotId;
        const selection = this.selections[pairId];
        if (!sessionId || !snapshotId || !selection || this.changePlanRevisions[pairId] !== revision) return;

        try {
          const response = await gateway.changePlan(
            sessionId,
            this.sessionToken!,
            snapshotId,
            cloneSelection(selection),
          );
          if (
            this.sessionId === sessionId
            && this.snapshotId === snapshotId
            && this.changePlanRevisions[pairId] === revision
          ) {
            this.changePlans[pairId] = response;
          }
        } catch (error) {
          if (this.changePlanRevisions[pairId] === revision) {
            this.invalidateSnapshot(error);
            this.addProgress('error', errorMessage(error));
          }
          throw error;
        } finally {
          if (this.changePlanRevisions[pairId] === revision) {
            this.changePlanPending[pairId] = false;
          }
        }
      },

      cancelChangePlanRequests(): void {
        for (const timer of debounceTimers.values()) clearTimeout(timer);
        debounceTimers.clear();
        this.changePlanPending = {};
        this.changePlanRevisions = {};
      },

      invalidateSnapshot(error: unknown): void {
        if (!(error instanceof ApiError) || error.code !== 'snapshot_conflict') return;
        this.cancelChangePlanRequests();
        this.snapshotId = null;
        this.changePlans = {};
        this.baselineChangePlans = {};
      },
    },
  });
}

export const useSessionStore = createSessionStore(defaultGateway);

function initialState(): SessionState {
  return {
    sessionId: null,
    sessionToken: null,
    expiresAt: null,
    files: [],
    pairs: [],
    pairingIssues: [],
    previewResults: [],
    previewStaging: [],
    previewFailures: [],
    previewFailureStaging: [],
    snapshotId: null,
    selections: {},
    changePlans: {},
    baselineChangePlans: {},
    changePlanPending: {},
    changePlanRevisions: {},
    resultSummary: null,
    selectedPairId: null,
    operation: 'idle',
    progressEvents: [],
  };
}

function toSessionFile(file: File): SessionFile {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  const kind = extension === 'ttml' ? 'ttml' : AUDIO_EXTENSIONS.has(extension) ? 'audio' : 'other';
  return { name: file.name, size: file.size, kind };
}

function previewJobError(job: PreviewJobResponse): string {
  return job.pair_failures?.[0]?.error.message ?? job.errors[0]?.message ?? 'Preview failed';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function cloneSelection(selection: SelectionPayload): SelectionPayload {
  return {
    pair_id: selection.pair_id,
    sources: Object.fromEntries(Object.entries(selection.sources).map(([source, ids]) => [source, [...ids]])),
  };
}

function clonePlanMap(plans: Record<string, ChangePlanResponse>): Record<string, ChangePlanResponse> {
  return Object.fromEntries(Object.entries(plans).map(([pairId, plan]) => [
    pairId,
    JSON.parse(JSON.stringify(plan)) as ChangePlanResponse,
  ]));
}
