import { defineStore } from 'pinia';

import { client } from '@/api/client';
import type { ApplySummary, FilePair, PreviewResult, ProgressEvent, SelectionPayload, SessionFile, WorkflowStep } from '@/api/types';

interface SessionState {
  sessionId: string | null;
  files: SessionFile[];
  pairs: FilePair[];
  previewResults: PreviewResult[];
  selections: Record<string, SelectionPayload>;
  resultSummary: ApplySummary | null;
  currentStep: WorkflowStep;
  selectedPairId: string | null;
  loading: boolean;
  progressEvents: ProgressEvent[];
}

const emptySelection = (pairId: string): SelectionPayload => ({
  pair_id: pairId,
  apple_music: [],
  qq_music: [],
  ncm_music: [],
  spotify: [],
});

export const useSessionStore = defineStore('session', {
  state: (): SessionState => ({
    sessionId: null,
    files: [],
    pairs: [],
    previewResults: [],
    selections: {},
    resultSummary: null,
    currentStep: 'upload',
    selectedPairId: null,
    loading: false,
    progressEvents: [],
  }),

  getters: {
    selectedPreview(state): PreviewResult | null {
      return state.previewResults.find((result) => result.pair_id === state.selectedPairId) ?? state.previewResults[0] ?? null;
    },
    selectionCount(state): number {
      return Object.values(state.selections).reduce((total, selection) => {
        return total + selection.apple_music.length + selection.qq_music.length + selection.ncm_music.length + selection.spotify.length;
      }, 0);
    },
    canPreview(state): boolean {
      return state.pairs.some((pair) => pair.ttml);
    },
    canApply(state): boolean {
      return state.previewResults.length > 0;
    },
  },

  actions: {
    async ensureSession() {
      if (this.sessionId) return;
      const response = await client.createSession();
      this.sessionId = response.session_id;
      this.addProgress('info', `会话已创建 ${response.session_id}`);
    },

    async uploadFiles(files: File[]) {
      await this.ensureSession();
      this.loading = true;
      try {
        const response = await client.uploadFiles(this.sessionId!, files);
        this.files = response.files;
        this.pairs = response.pairs;
        this.selectedPairId = response.pairs[0]?.id ?? null;
        this.previewResults = [];
        this.resultSummary = null;
        this.selections = {};
        this.currentStep = 'upload';
        this.addProgress('success', `已识别 ${response.pairs.length} 组 TTML`);
      } catch (error) {
        this.addProgress('error', error instanceof Error ? error.message : String(error));
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async previewAll() {
      await this.ensureSession();
      this.loading = true;
      try {
        this.addProgress('info', '开始 dry-run 预览');
        const response = await client.preview(this.sessionId!, this.pairs);
        this.previewResults = response.results;
        this.selections = Object.fromEntries(response.results.map((result) => [result.pair_id, defaultSelection(result)]));
        this.selectedPairId = response.results[0]?.pair_id ?? null;
        this.currentStep = 'preview';
        this.addProgress('success', `预览完成 ${response.results.length}/${this.pairs.length}`);
      } catch (error) {
        this.addProgress('error', error instanceof Error ? error.message : String(error));
        throw error;
      } finally {
        this.loading = false;
      }
    },

    toggleCandidate(pairId: string, source: keyof Omit<SelectionPayload, 'pair_id'>, candidateId: string) {
      const selection = this.selections[pairId] ?? emptySelection(pairId);
      const values = new Set(selection[source]);
      if (values.has(candidateId)) {
        values.delete(candidateId);
      } else {
        values.add(candidateId);
      }
      this.selections[pairId] = { ...selection, [source]: Array.from(values) };
    },

    acceptBestForAll() {
      this.selections = Object.fromEntries(this.previewResults.map((result) => [result.pair_id, defaultSelection(result)]));
      this.addProgress('info', '已选择全部最佳候选');
    },

    async applySelections() {
      await this.ensureSession();
      this.loading = true;
      try {
        const payload = Object.values(this.selections);
        this.addProgress('info', `写入 ${payload.length} 组选择`);
        this.resultSummary = await client.apply(this.sessionId!, payload, this.previewResults);
        this.currentStep = 'result';
        this.addProgress('success', '写入流程已完成');
      } catch (error) {
        this.addProgress('error', error instanceof Error ? error.message : String(error));
        throw error;
      } finally {
        this.loading = false;
      }
    },

    setStep(step: WorkflowStep) {
      this.currentStep = step;
    },

    selectPair(pairId: string) {
      this.selectedPairId = pairId;
    },

    addProgress(level: ProgressEvent['level'], message: string) {
      this.progressEvents.unshift({ id: `${Date.now()}-${this.progressEvents.length}`, level, message, at: new Date().toLocaleTimeString() });
      this.progressEvents = this.progressEvents.slice(0, 8);
    },
  },
});

function defaultSelection(result: PreviewResult): SelectionPayload {
  return {
    pair_id: result.pair_id,
    apple_music: result.apple_music.best.map((candidate) => candidate.id),
    qq_music: result.qq_music.best.map((candidate) => candidate.id),
    ncm_music: result.ncm_music.best.map((candidate) => candidate.id),
    spotify: result.spotify.best.map((candidate) => candidate.id),
  };
}

