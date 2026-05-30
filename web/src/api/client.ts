import type {
  ApplySummary,
  CandidateBase,
  FilePair,
  PreviewResult,
  SelectionPayload,
  SessionFile,
  SourcePreview,
  UploadResponse,
} from './types';

const AUDIO_EXTENSIONS = new Set(['aac', 'aif', 'aiff', 'alac', 'ape', 'flac', 'm4a', 'm4b', 'm4p', 'mp3', 'mp4', 'ogg', 'opus', 'wav', 'wma']);
export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';
const USE_MOCK = import.meta.env.VITE_USE_MOCK_API === '1';

const delay = (ms = 160) => new Promise((resolve) => window.setTimeout(resolve, ms));

export interface IdMatchClient {
  createSession(): Promise<{ session_id: string }>;
  uploadFiles(sessionId: string, files: File[]): Promise<UploadResponse>;
  preview(sessionId: string, pairs: FilePair[]): Promise<{ results: PreviewResult[] }>;
  apply(sessionId: string, selections: SelectionPayload[], previews: PreviewResult[]): Promise<ApplySummary>;
}


export function downloadAllUrl(sessionId: string): string {
  return `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/download`;
}

export function downloadFileUrl(sessionId: string, filename: string): string {
  return `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/download/${encodeURIComponent(filename)}`;
}
export class HttpIdMatchClient implements IdMatchClient {
  async createSession(): Promise<{ session_id: string }> {
    return request('/sessions', { method: 'POST' });
  }

  async uploadFiles(sessionId: string, files: File[]): Promise<UploadResponse> {
    const body = new FormData();
    for (const file of files) {
      body.append('files', file);
    }
    return request(`/sessions/${encodeURIComponent(sessionId)}/upload`, { method: 'POST', body });
  }

  async preview(sessionId: string): Promise<{ results: PreviewResult[] }> {
    return request(`/sessions/${encodeURIComponent(sessionId)}/preview`, { method: 'POST' });
  }

  async apply(sessionId: string, selections: SelectionPayload[]): Promise<ApplySummary> {
    return request(`/sessions/${encodeURIComponent(sessionId)}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selections }),
    });
  }
}

export class MockIdMatchClient implements IdMatchClient {
  async createSession(): Promise<{ session_id: string }> {
    await delay(40);
    return { session_id: `mock-${Date.now().toString(36)}` };
  }

  async uploadFiles(_sessionId: string, files: File[]): Promise<UploadResponse> {
    await delay();
    const normalized = files.map(toSessionFile);
    return { files: normalized, pairs: pairFiles(normalized) };
  }

  async preview(_sessionId: string, pairs: FilePair[]): Promise<{ results: PreviewResult[] }> {
    await delay(260);
    return { results: pairs.filter((pair) => pair.ttml).map((pair, index) => makePreview(pair, index)) };
  }

  async apply(_sessionId: string, _selections: SelectionPayload[], previews: PreviewResult[]): Promise<ApplySummary> {
    await delay(220);
    return {
      succeeded: previews.length,
      failed: 0,
      skipped: 0,
      files: previews.map((preview) => ({
        pair_id: preview.pair_id,
        ttml: preview.ttml,
        status: 'success',
        metadata_written: Object.keys(preview.changes.added),
      })),
    };
  }
}

export const client: IdMatchClient = USE_MOCK ? new MockIdMatchClient() : new HttpIdMatchClient();

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === 'string' ? payload.detail : detail;
    } catch {
      // Keep the HTTP status text when the backend returns a non-JSON error.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function toSessionFile(file: File): SessionFile {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  const kind = extension === 'ttml' ? 'ttml' : AUDIO_EXTENSIONS.has(extension) ? 'audio' : 'other';
  return { name: file.name, size: file.size, kind };
}

function pairFiles(files: SessionFile[]): FilePair[] {
  const audioByStem = new Map<string, SessionFile>();
  const pairs: FilePair[] = [];

  for (const file of files) {
    if (file.kind === 'audio' && !audioByStem.has(stem(file.name))) {
      audioByStem.set(stem(file.name), file);
    }
  }

  for (const file of files.filter((item) => item.kind === 'ttml')) {
    const audio = audioByStem.get(stem(file.name)) ?? null;
    pairs.push({
      id: `pair-${pairs.length + 1}`,
      ttml: file.name,
      audio: audio?.name ?? null,
      status: audio ? 'paired' : 'ttml_only',
    });
  }

  return pairs;
}

function stem(filename: string): string {
  const clean = filename.replace(/\.[^.]+$/, '');
  return clean.trim().toLocaleLowerCase();
}

function makePreview(pair: FilePair, index: number): PreviewResult {
  const title = titleFromFile(pair.ttml ?? `Track ${index + 1}`);
  const artists = index % 2 === 0 ? ['Lady Gaga'] : ['HOYO-MiX', 'Sān-Z'];
  const album = index % 2 === 0 ? 'Apple Music Live' : 'I Ask - Single';

  const appleCn = candidate('apple_music', `${pair.id}-am-cn`, title, artists, album, 'cn', 98);
  const appleUs = candidate('apple_music', `${pair.id}-am-us`, title, artists, album, 'us', 91);
  const spotifyUs = candidate('spotify', `${pair.id}-sp-us`, title, artists, album, 'US', 96);
  const spotifyJp = candidate('spotify', `${pair.id}-sp-jp`, title, artists, album, 'JP', 90);
  const qq = candidate('qq_music', `${pair.id}-qq`, title, artists, album, undefined, 94);
  const ncm = candidate('ncm_music', `${pair.id}-ncm`, title, artists, album, undefined, 89);

  return {
    pair_id: pair.id,
    ttml: pair.ttml ?? '',
    audio: pair.audio,
    apple_music: source([appleCn, appleUs], { cn: [appleCn], us: [appleUs] }),
    qq_music: source([qq]),
    ncm_music: source([ncm]),
    spotify: source([spotifyUs, spotifyJp], undefined, { US: [spotifyUs], JP: [spotifyJp] }),
    changes: {
      added: {
        musicName: [title],
        artists,
        album: [album],
        appleMusicId: [appleCn.id, appleUs.id],
        qqMusicId: [qq.id],
        ncmMusicId: [ncm.id],
        spotifyId: [spotifyUs.id, spotifyJp.id],
        isrc: [`USUM7${index}260001`],
      },
      replaced: index === 0 ? { musicName: ['* -> ' + title] } : {},
      skipped: pair.audio ? {} : { audio: ['未上传同名音频，使用 TTML-only 元数据'] },
    },
  };
}

function titleFromFile(filename: string): string {
  return filename.replace(/\.[^.]+$/, '').trim() || 'Untitled';
}

function candidate(sourceName: CandidateBase['source'], id: string, title: string, artists: string[], album: string, region: string | undefined, score: number): CandidateBase {
  return {
    id,
    title,
    artists,
    album,
    region,
    score,
    source: sourceName,
    isrc: sourceName === 'qq_music' || sourceName === 'ncm_music' ? undefined : `USUM7${score}0001`,
    duration_ms: 206000 + score,
    release_date: '2024-05-23',
  };
}

function source(
  candidates: CandidateBase[],
  candidatesByStorefront: Record<string, CandidateBase[]> = {},
  candidatesByMarket: Record<string, CandidateBase[]> = {},
): SourcePreview {
  return {
    best: candidates.slice(0, Math.min(2, candidates.length)),
    candidates,
    candidates_by_storefront: candidatesByStorefront,
    candidates_by_market: candidatesByMarket,
    errors: [],
  };
}

