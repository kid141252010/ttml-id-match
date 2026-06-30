export type WorkflowStep = 'upload' | 'preview' | 'result';

export type PairStatus = 'paired' | 'ttml_only' | 'orphan_audio';

export interface SessionFile {
  name: string;
  size: number;
  kind: 'audio' | 'ttml' | 'other';
}

export interface FilePair {
  id: string;
  ttml: string | null;
  audio: string | null;
  status: PairStatus;
}

export interface CandidateBase {
  id: string;
  title: string;
  artists: string[];
  album: string;
  region?: string;
  isrc?: string;
  duration_ms?: number;
  release_date?: string;
  score: number;
  source: 'apple_music' | 'qq_music' | 'ncm_music' | 'spotify';
}

export interface SourcePreview {
  best: CandidateBase[];
  candidates: CandidateBase[];
  candidates_by_storefront: Record<string, CandidateBase[]>;
  candidates_by_market: Record<string, CandidateBase[]>;
  errors: string[];
}

export interface ChangeSet {
  added: Record<string, string[]>;
  replaced: Record<string, string[]>;
  skipped: Record<string, string[]>;
}

export interface PreviewResult {
  pair_id: string;
  ttml: string;
  audio: string | null;
  apple_music: SourcePreview;
  qq_music: SourcePreview;
  ncm_music: SourcePreview;
  spotify: SourcePreview;
  changes: ChangeSet;
}

export type PreviewJobStatus = 'pending' | 'running' | 'complete' | 'failed';

export interface PreviewJobResponse {
  job_id: string;
  status: PreviewJobStatus;
  total: number;
  completed: number;
  results: PreviewResult[];
  error?: string | null;
}

export interface SelectionPayload {
  pair_id: string;
  apple_music: string[];
  qq_music: string[];
  ncm_music: string[];
  spotify: string[];
}

export interface ApplySummary {
  succeeded: number;
  failed: number;
  skipped: number;
  files: Array<{
    pair_id: string;
    ttml: string;
    status: 'success' | 'failed' | 'skipped';
    metadata_written: string[];
    error?: string | null;
  }>;
}

export interface UploadResponse {
  files: SessionFile[];
  pairs: FilePair[];
}

export interface ProgressEvent {
  id: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  at: string;
}

