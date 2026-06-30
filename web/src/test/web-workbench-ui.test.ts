import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

import UploadView from '@/views/UploadView.vue';
import PreviewView from '@/views/PreviewView.vue';
import ResultView from '@/views/ResultView.vue';
import { useSessionStore } from '@/stores/session';

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, client: new actual.MockIdMatchClient() };
});

describe('workbench-oriented web UI', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
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
