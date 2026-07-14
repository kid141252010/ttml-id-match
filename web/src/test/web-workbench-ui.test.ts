import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

import App from '@/App.vue';
import CandidateCard from '@/components/CandidateCard.vue';
import DiffViewer from '@/components/DiffViewer.vue';
import PairList from '@/components/PairList.vue';
import UploadView from '@/views/UploadView.vue';
import PreviewView from '@/views/PreviewView.vue';
import ResultView from '@/views/ResultView.vue';
import { useSessionStore } from '@/stores/session';

const routerHarness = vi.hoisted(() => ({
  route: { name: 'upload' },
  push: vi.fn(),
}));

vi.mock('vue-router', () => ({
  RouterView: { template: '<div data-testid="router-view" />' },
  useRoute: () => routerHarness.route,
  useRouter: () => ({ push: routerHarness.push }),
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  const summary = {
    input_sha256: 'before', output_sha256: 'after', final_text: '<tt>after</tt>', changed: true,
    metadata: { added: { appleMusicId: ['song-42'] }, replaced: {}, skipped: {}, changed: true },
    normalization: {
      language_changed: false, body_text_changed: false,
      removed_translations: 0, removed_transliterations: 0, changed: false,
    },
  };
  const preview = {
    pair_id: 'pair-disease',
    files: {
      ttml: { filename: 'Disease.ttml', sha256: 'before' },
      audio: { filename: 'Disease.flac', sha256: 'audio' },
    },
    sources: {
      apple_music: {
        source: 'apple_music',
        candidates: [{
          id: 'us:song-42', source: 'apple_music', title: 'Disease', artists: ['Lady Gaga'],
          album: 'Disease', aliases: [], identifiers: { isrc: 'USUM72407015' }, group: 'us',
          rank: 1, recommended: true,
          evidence: [{ field: 'title', relation: 'exact', expected: 'Disease', actual: 'Disease' }],
          duration_ms: 229997, release_date: '2024-10-25',
        }],
        groups: { us: ['us:song-42'] }, recommended_ids: ['us:song-42'], warnings: [],
      },
    },
    default_selection: { pair_id: 'pair-disease', sources: { apple_music: ['us:song-42'] } },
    baseline_change_plan: summary,
  };
  const boundary = {
    createSession: vi.fn(async () => ({
      session_id: 'ui-session',
      session_token: 'ui-token',
      expires_at: '2026-07-14T00:00:00Z',
    })),
    deleteSession: vi.fn(async () => undefined),
    uploadFiles: vi.fn(async () => ({
      pairs: [
        { pair_id: 'pair-disease', status: 'paired', ttml_path: 'Disease.ttml', audio_path: 'Disease.flac', audio_candidates: [] },
        { pair_id: 'pair-lyrics', status: 'ttml_only', ttml_path: 'Lyrics Only.ttml', audio_path: null, audio_candidates: [] },
      ],
      issues: [],
    })),
    createPreviewJob: vi.fn(async () => ({
      job_id: 'job-1', status: 'pending', total: 1, completed: 0,
      results: [], pair_failures: [], errors: [], snapshot_id: null,
    })),
    getPreviewJob: vi.fn(),
    stepPreviewJob: vi.fn(async () => ({
      job_id: 'job-1', status: 'completed', total: 1, completed: 1,
      results: [preview], pair_failures: [], errors: [], snapshot_id: 'snapshot-1',
    })),
    changePlan: vi.fn(async () => ({
      snapshot_id: 'snapshot-1', pair_id: 'pair-disease', ...summary,
    })),
    apply: vi.fn(async () => ({
      snapshot_id: 'snapshot-1', succeeded: 1, failed: 0, skipped: 0,
      files: [{
        pair_id: 'pair-disease', ttml: 'Disease.ttml', status: 'applied',
        output_sha256: 'after', backup: 'Disease.ttml.bak', error: null,
      }],
    })),
    downloadAll: vi.fn(async () => new Blob()),
    downloadFile: vi.fn(async () => new Blob()),
  };
  return { ...actual, client: boundary, gateway: boundary };
});

describe('workbench-oriented web UI', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    routerHarness.route.name = 'upload';
    routerHarness.push.mockReset();
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
  });

  it('keeps the active workflow step derived from the route', async () => {
    routerHarness.route.name = 'preview';
    const store = useSessionStore();
    const wrapper = mount(App, { global: { stubs: { NIcon: true } } });

    await wrapper.vm.$nextTick();

    const steps = wrapper.findAll('.step-item');
    expect(steps.find((step) => step.text().includes('预览'))?.classes()).toContain('active');
    expect(steps.find((step) => step.text().includes('下载'))?.classes()).not.toContain('active');
  });

  it('explains a recommended candidate with rank and evidence instead of a score percentage', () => {
    const wrapper = mount(CandidateCard, {
      props: {
        candidate: {
          id: 'song-42',
          source: 'spotify',
          title: 'Disease',
          artists: ['Lady Gaga'],
          album: 'Disease',
          aliases: [],
          identifiers: { isrc: 'USUM72407015' },
          group: null,
          rank: 1,
          recommended: true,
          evidence: [
            { field: 'title', relation: 'exact', expected: 'Disease', actual: 'Disease' },
            { field: 'artist', relation: 'contains', expected: 'Lady Gaga', actual: 'Lady Gaga' },
          ],
          duration_ms: 229997,
          release_date: '2024-10-25',
        },
        selected: true,
      },
      global: { stubs: { NIcon: true } },
    });

    expect(wrapper.text()).toContain('#1');
    expect(wrapper.text()).toContain('推荐');
    expect(wrapper.text()).toContain('标题精确匹配');
    expect(wrapper.text()).toContain('艺术家包含匹配');
    expect(wrapper.text()).not.toContain('%');
  });

  it('shows metadata and normalization from the current selection change plan', () => {
    const wrapper = mount(DiffViewer, {
      props: {
        plan: {
          snapshot_id: 'snapshot-1',
          pair_id: 'pair-1',
          input_sha256: 'before',
          output_sha256: 'after',
          final_text: '<tt><head><metadata><meta key="spotifyId" value="song-42" /></metadata></head></tt>',
          changed: true,
          metadata: {
            added: { spotifyId: ['song-42'] },
            replaced: { musicName: ['Disease'] },
            skipped: {},
            changed: true,
          },
          normalization: {
            language_changed: true,
            body_text_changed: true,
            removed_translations: 2,
            removed_transliterations: 1,
            changed: true,
          },
        },
      } as any,
    });

    expect(wrapper.text()).toContain('spotifyId');
    expect(wrapper.text()).toContain('Disease');
    expect(wrapper.find('[data-testid="normalization-summary"]').text()).toContain('语言标签已规范化');
    expect(wrapper.find('[data-testid="normalization-summary"]').text()).toContain('移除 2 个翻译层');
    expect(wrapper.find('[data-testid="normalization-summary"]').text()).toContain('移除 1 个音译层');
    expect(wrapper.find('[data-testid="final-ttml"]').text()).toContain('value="song-42"');
  });

  it('shows ambiguous audio candidates as a blocking pairing issue', () => {
    const wrapper = mount(PairList, {
      props: {
        pairs: [{
          id: 'pair-ambiguous',
          ttml: 'Disease.ttml',
          audio: null,
          status: 'ambiguous',
          audio_candidates: ['Disease.flac', 'disease.mp3'],
        }],
      },
      global: { stubs: { NIcon: true } },
    });

    expect(wrapper.text()).toContain('音频冲突');
    expect(wrapper.text()).toContain('Disease.flac');
    expect(wrapper.text()).toContain('disease.mp3');
    expect(wrapper.text()).not.toContain('TTML-only');
  });

  it('marks pair-level preview failures in the queue', () => {
    const wrapper = mount(PairList, {
      props: {
        pairs: [{
          id: 'pair-failed',
          ttml: 'Broken.ttml',
          audio: null,
          status: 'ttml_only',
          audio_candidates: [],
        }],
        failedIds: ['pair-failed'],
      },
      global: { stubs: { NIcon: true } },
    });

    expect(wrapper.text()).toContain('预览失败');
    expect(wrapper.find('.status-preview-failed').exists()).toBe(true);
  });

  it('shows upload workbench summary after files are uploaded', async () => {
    const store = useSessionStore();
    await store.uploadFiles([
      new File(['ttml'], 'Disease.ttml', { type: 'application/xml' }),
      new File(['audio'], 'Disease.flac', { type: 'audio/flac' }),
      new File(['ttml'], 'Lyrics Only.ttml', { type: 'application/xml' }),
    ]);

    const wrapper = mount(UploadView, { global: { stubs: { NIcon: true } } });

    expect(wrapper.find('[data-testid="upload-workbench-summary"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="upload-file-review"]').text()).toContain('Disease.ttml');
    expect(wrapper.find('[data-testid="upload-file-review"]').text()).toContain('Lyrics Only.ttml');
    expect(wrapper.find('[data-testid="upload-pairing-context"]').text()).toContain('仅歌词');
  });

  it('renders preview candidate rows with source counts and write preview context', async () => {
    const store = useSessionStore();
    await store.uploadFiles([
      new File(['ttml'], 'Disease.ttml', { type: 'application/xml' }),
      new File(['audio'], 'Disease.flac', { type: 'audio/flac' }),
    ]);
    await store.previewAll();

    const wrapper = mount(PreviewView, { global: { stubs: { NIcon: true } } });

    expect(wrapper.find('[data-testid="source-switcher"]').text()).toContain('Apple Music');
    expect(wrapper.findAll('[data-testid="candidate-row"]').length).toBeGreaterThan(0);
    expect(wrapper.find('[data-testid="candidate-row"]').text()).toContain('Disease');
    expect(wrapper.find('[data-testid="write-preview-context"]').exists()).toBe(true);
  });

  it('renders result review summary and per-file rows after applying selections', async () => {
    const store = useSessionStore();
    await store.uploadFiles([
      new File(['ttml'], 'Disease.ttml', { type: 'application/xml' }),
      new File(['audio'], 'Disease.flac', { type: 'audio/flac' }),
    ]);
    await store.previewAll();
    await store.applySelections();

    const wrapper = mount(ResultView, { global: { stubs: { NIcon: true } } });

    expect(wrapper.find('[data-testid="result-workbench-summary"]').text()).toContain('成功');
    expect(wrapper.find('[data-testid="result-file-review"]').text()).toContain('Disease.ttml');
    expect(wrapper.find('[data-testid="result-file-review"]').text()).toContain('单文件下载');
  });
});
