import type {
  ApplySummary,
  ChangePlanResponse,
  PairingPlanResponse,
  PreviewJobResponse,
  SelectionPayload,
} from './types';
import {
  adaptApplySummary,
  adaptChangePlan,
  adaptPairingPlan,
  adaptPreviewJob,
} from './adapter';
import type {
  ApplyResponseDto,
  ChangePlanResponseDto,
  ErrorResponseDto,
  PairingPlanResponseDto,
  PreviewJobDto,
  SessionResponseDto,
} from './dto';

export const API_BASE = normalizeBase(import.meta.env.VITE_API_BASE ?? '/api/v2');

export interface IdMatchGateway {
  createSession(): Promise<{ session_id: string }>;
  deleteSession(sessionId: string): Promise<void>;
  uploadFiles(sessionId: string, files: File[]): Promise<PairingPlanResponse>;
  createPreviewJob(sessionId: string): Promise<PreviewJobResponse>;
  getPreviewJob(sessionId: string, jobId: string): Promise<PreviewJobResponse>;
  stepPreviewJob(sessionId: string, jobId: string): Promise<PreviewJobResponse>;
  changePlan(sessionId: string, snapshotId: string, selection: SelectionPayload): Promise<ChangePlanResponse>;
  apply(sessionId: string, snapshotId: string, selections: SelectionPayload[]): Promise<ApplySummary>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = 'http_error',
    readonly retryable = false,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class HttpIdMatchGateway implements IdMatchGateway {
  constructor(private readonly baseUrl = API_BASE) {}

  createSession(): Promise<{ session_id: string }> {
    return this.request<SessionResponseDto>('/sessions', { method: 'POST' });
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.request(`/sessions/${segment(sessionId)}`, { method: 'DELETE' });
  }

  async uploadFiles(sessionId: string, files: File[]): Promise<PairingPlanResponse> {
    const body = new FormData();
    for (const file of files) body.append('files', file);
    return adaptPairingPlan(await this.request<PairingPlanResponseDto>(
      `/sessions/${segment(sessionId)}/files`, { method: 'POST', body },
    ));
  }

  async createPreviewJob(sessionId: string): Promise<PreviewJobResponse> {
    return adaptPreviewJob(await this.request<PreviewJobDto>(
      `/sessions/${segment(sessionId)}/preview-jobs`, { method: 'POST' },
    ));
  }

  async getPreviewJob(sessionId: string, jobId: string): Promise<PreviewJobResponse> {
    return adaptPreviewJob(await this.request<PreviewJobDto>(
      `/sessions/${segment(sessionId)}/preview-jobs/${segment(jobId)}`, { method: 'GET' },
    ));
  }

  async stepPreviewJob(sessionId: string, jobId: string): Promise<PreviewJobResponse> {
    return adaptPreviewJob(await this.request<PreviewJobDto>(
      `/sessions/${segment(sessionId)}/preview-jobs/${segment(jobId)}/steps`, { method: 'POST' },
    ));
  }

  async changePlan(sessionId: string, snapshotId: string, selection: SelectionPayload): Promise<ChangePlanResponse> {
    return adaptChangePlan(await this.request<ChangePlanResponseDto>(
      `/sessions/${segment(sessionId)}/change-plans`, jsonRequest({ snapshot_id: snapshotId, selection }),
    ));
  }

  async apply(sessionId: string, snapshotId: string, selections: SelectionPayload[]): Promise<ApplySummary> {
    return adaptApplySummary(await this.request<ApplyResponseDto>(
      `/sessions/${segment(sessionId)}/apply`, jsonRequest({ snapshot_id: snapshotId, selections }),
    ));
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(`${normalizeBase(this.baseUrl)}${path}`, init);
    if (!response.ok) throw await apiError(response);
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
}

export const gateway: IdMatchGateway = new HttpIdMatchGateway();

export function downloadAllUrl(sessionId: string): string {
  return `${API_BASE}/sessions/${segment(sessionId)}/outputs.zip`;
}

export function downloadFileUrl(sessionId: string, filename: string): string {
  return `${API_BASE}/sessions/${segment(sessionId)}/outputs/${segment(filename)}`;
}

function jsonRequest(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function normalizeBase(value: string): string {
  const trimmed = value.replace(/\/+$/, '');
  if (trimmed.endsWith('/api/v2')) return trimmed;
  if (trimmed.endsWith('/api')) return `${trimmed}/v2`;
  return trimmed;
}

function segment(value: string): string {
  return encodeURIComponent(value);
}

async function apiError(response: Response): Promise<ApiError> {
  const fallback = `${response.status} ${response.statusText}`.trim();
  try {
    const payload = await response.json() as Partial<ErrorResponseDto> & {
      detail?: string | Partial<ErrorResponseDto>;
    };
    if (payload.message || payload.code) {
      return new ApiError(
        payload.message ?? fallback,
        response.status,
        payload.code ?? 'http_error',
        payload.retryable ?? false,
        payload.details ?? {},
      );
    }
    if (typeof payload.detail === 'string') return new ApiError(payload.detail, response.status);
    const detail = payload.detail;
    if (detail && typeof detail === 'object') {
      return new ApiError(
        detail.message ?? fallback,
        response.status,
        detail.code ?? 'http_error',
        detail.retryable ?? false,
        detail.details ?? {},
      );
    }
  } catch {
    // The status line remains useful when an intermediary returns non-JSON.
  }
  return new ApiError(fallback, response.status);
}
