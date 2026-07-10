import type {
  ApplyResponseDto,
  ChangePlanResponseDto,
  PairingPlanResponseDto,
  PreviewJobDto,
} from './dto';
import type {
  ApplySummary,
  Candidate,
  ChangePlanResponse,
  ChangePlanSummary,
  PairPreview,
  PairingPlanResponse,
  PreviewJobResponse,
  SourceResult,
} from './types';

export function adaptPairingPlan(dto: PairingPlanResponseDto): PairingPlanResponse {
  return {
    pairs: (dto.pairs ?? []).map((pair) => ({
      pair_id: pair.pair_id,
      status: pair.status,
      ttml_path: pair.ttml_path,
      audio_path: pair.audio_path ?? null,
      audio_candidates: [...(pair.audio_candidates ?? [])],
    })),
    issues: (dto.issues ?? []).map((issue) => ({
      code: issue.code,
      pair_id: issue.pair_id,
      ttml_path: issue.ttml_path,
      audio_candidates: [...(issue.audio_candidates ?? [])],
    })),
  };
}

export function adaptPreviewJob(dto: PreviewJobDto): PreviewJobResponse {
  return {
    job_id: dto.job_id,
    status: dto.status,
    total: dto.total,
    completed: dto.completed,
    results: (dto.results ?? []).map(adaptPairPreview),
    errors: (dto.errors ?? []).map((error) => ({
      code: error.code,
      message: error.message,
      retryable: error.retryable,
      details: error.details ?? {},
    })),
    snapshot_id: dto.snapshot_id ?? null,
  };
}

export function adaptChangePlan(dto: ChangePlanResponseDto): ChangePlanResponse {
  return {
    snapshot_id: dto.snapshot_id,
    pair_id: dto.pair_id,
    ...adaptChangePlanSummary(dto),
  };
}

export function adaptApplySummary(dto: ApplyResponseDto): ApplySummary {
  return {
    snapshot_id: dto.snapshot_id,
    succeeded: dto.succeeded,
    failed: dto.failed,
    skipped: dto.skipped,
    files: (dto.files ?? []).map((file) => ({
      pair_id: file.pair_id,
      ttml: file.ttml,
      status: file.status,
      output_sha256: file.output_sha256 ?? null,
      backup: file.backup ?? null,
      error: file.error ? {
        code: file.error.code,
        message: file.error.message,
        retryable: file.error.retryable,
        details: file.error.details ?? {},
      } : null,
    })),
  };
}

function adaptPairPreview(dto: PreviewJobDto['results'] extends Array<infer T> | undefined ? T : never): PairPreview {
  return {
    pair_id: dto.pair_id,
    files: {
      ttml: { ...dto.files.ttml },
      audio: dto.files.audio ? { ...dto.files.audio } : null,
    },
    sources: Object.fromEntries(
      Object.entries(dto.sources ?? {}).map(([key, result]) => [key, adaptSourceResult(result)]),
    ),
    default_selection: {
      pair_id: dto.default_selection.pair_id,
      sources: cloneStringArrays(dto.default_selection.sources ?? {}),
    },
    baseline_change_plan: adaptChangePlanSummary(dto.baseline_change_plan),
  };
}

function adaptSourceResult(dto: NonNullable<PreviewJobDto['results']>[number]['sources'] extends Record<string, infer T> | undefined ? T : never): SourceResult {
  return {
    source: dto.source,
    candidates: (dto.candidates ?? []).map(adaptCandidate),
    groups: cloneStringArrays(dto.groups ?? {}),
    recommended_ids: [...(dto.recommended_ids ?? [])],
    warnings: [...(dto.warnings ?? [])],
  };
}

function adaptCandidate(dto: NonNullable<NonNullable<PreviewJobDto['results']>[number]['sources']>[string]['candidates'] extends Array<infer T> | undefined ? T : never): Candidate {
  return {
    id: dto.id,
    source: dto.source,
    title: dto.title ?? null,
    artists: [...(dto.artists ?? [])],
    album: dto.album ?? null,
    aliases: [...(dto.aliases ?? [])],
    identifiers: { ...(dto.identifiers ?? {}) },
    group: dto.group ?? null,
    rank: dto.rank,
    recommended: dto.recommended,
    evidence: (dto.evidence ?? []).map((evidence) => ({
      field: evidence.field,
      relation: evidence.relation,
      expected: evidence.expected ?? null,
      actual: evidence.actual ?? null,
    })),
    duration_ms: dto.duration_ms ?? null,
    release_date: dto.release_date ?? null,
  };
}

function adaptChangePlanSummary(dto: {
  input_sha256: string;
  output_sha256: string;
  final_text: string;
  changed: boolean;
  metadata: {
    added?: Record<string, string[]>;
    replaced?: Record<string, string[]>;
    skipped?: Record<string, string[]>;
    changed: boolean;
  };
  normalization: ChangePlanSummary['normalization'];
}): ChangePlanSummary {
  return {
    input_sha256: dto.input_sha256,
    output_sha256: dto.output_sha256,
    final_text: dto.final_text,
    changed: dto.changed,
    metadata: {
      added: cloneStringArrays(dto.metadata.added ?? {}),
      replaced: cloneStringArrays(dto.metadata.replaced ?? {}),
      skipped: cloneStringArrays(dto.metadata.skipped ?? {}),
      changed: dto.metadata.changed,
    },
    normalization: { ...dto.normalization },
  };
}

function cloneStringArrays(value: Record<string, string[]>): Record<string, string[]> {
  return Object.fromEntries(Object.entries(value).map(([key, items]) => [key, [...items]]));
}
