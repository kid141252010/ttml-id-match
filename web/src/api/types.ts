export type WorkflowStep = 'upload' | 'preview' | 'result';
export type OperationState = 'idle' | 'uploading' | 'previewing' | 'applying';
export type SourceKey = string;

export type PairStatus = 'paired' | 'ttml_only' | 'ambiguous';

export interface SessionCredentials {
  session_id: string;
  session_token: string;
  expires_at: string;
}

export interface SessionFile {
  name: string;
  size: number;
  kind: 'audio' | 'ttml' | 'other';
}

/** Wire representation returned by POST /files. */
export interface PairingPair {
  pair_id: string;
  status: PairStatus;
  ttml_path: string;
  audio_path: string | null;
  audio_candidates: string[];
}

/** UI representation. Pairing decisions always come from the server. */
export interface FilePair {
  id: string;
  ttml: string;
  audio: string | null;
  status: PairStatus;
  audio_candidates: string[];
}

export interface PairingIssue {
  code: string;
  pair_id: string;
  ttml_path: string;
  audio_candidates: string[];
}

export interface PairingPlanResponse {
  pairs: PairingPair[];
  issues: PairingIssue[];
}

export interface Evidence {
  field: string;
  relation: string;
  expected: string | null;
  actual: string | null;
}

export interface Candidate {
  id: string;
  source: SourceKey;
  title: string | null;
  artists: string[];
  album: string | null;
  aliases: string[];
  identifiers: Record<string, string>;
  group: string | null;
  rank: number;
  recommended: boolean;
  evidence: Evidence[];
  duration_ms: number | null;
  release_date: string | null;
}

export type CandidateBase = Candidate;

export interface SourceResult {
  source: SourceKey;
  candidates: Candidate[];
  groups: Record<string, string[]>;
  recommended_ids: string[];
  warnings: string[];
}

export interface SelectionPayload {
  pair_id: string;
  sources: Record<SourceKey, string[]>;
}

export interface ChangeSet {
  added: Record<string, string[]>;
  replaced: Record<string, string[]>;
  skipped: Record<string, string[]>;
}

export interface MetadataChangeSummary extends ChangeSet {
  changed: boolean;
}

export interface NormalizationSummary {
  language_changed: boolean;
  body_text_changed: boolean;
  removed_translations: number;
  removed_transliterations: number;
  changed: boolean;
}

export interface ChangePlanSummary {
  input_sha256: string;
  output_sha256: string;
  final_text: string;
  changed: boolean;
  metadata: MetadataChangeSummary;
  normalization: NormalizationSummary;
}

export interface ChangePlanResponse extends ChangePlanSummary {
  snapshot_id: string;
  pair_id: string;
}

export interface PairFile {
  filename: string;
  sha256: string;
}

export interface PairFiles {
  ttml: PairFile;
  audio: PairFile | null;
}

export interface PairPreview {
  pair_id: string;
  files: PairFiles;
  sources: Record<SourceKey, SourceResult>;
  default_selection: SelectionPayload;
  baseline_change_plan: ChangePlanSummary;
}

export interface PairPreviewFailure {
  pair_id: string;
  ttml_path: string;
  audio_path: string | null;
  error: ApiErrorPayload;
}

export type PreviewResult = PairPreview;

export type PreviewJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed';

export interface ApiErrorPayload {
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface PreviewJobResponse {
  job_id: string;
  status: PreviewJobStatus;
  total: number;
  completed: number;
  results: PairPreview[];
  pair_failures: PairPreviewFailure[];
  errors: ApiErrorPayload[];
  snapshot_id: string | null;
}

export type ApplyFileStatus = 'applied' | 'unchanged' | 'failed';

export interface ApplyFileResult {
  pair_id: string;
  ttml: string;
  status: ApplyFileStatus;
  output_sha256: string | null;
  backup: string | null;
  error: ApiErrorPayload | null;
}

export interface ApplySummary {
  snapshot_id: string;
  succeeded: number;
  failed: number;
  skipped: number;
  files: ApplyFileResult[];
}

export type UploadResponse = PairingPlanResponse;

export interface ProgressEvent {
  id: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  at: string;
}
