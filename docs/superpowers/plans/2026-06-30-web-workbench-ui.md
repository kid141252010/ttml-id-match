# Web Workbench UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the Vue web UI into a restrained batch workbench that improves upload review, candidate scanning, and result inspection.

**Architecture:** Keep the existing Vue Router and Pinia workflow. Refactor view templates and shared CSS around reusable workbench primitives, with component tests covering structural behavior and mock workflow data.

**Tech Stack:** Vue 3, Vite, Pinia, Vue Router, Naive UI, lucide-vue-next, Vitest, Vue Test Utils, jsdom.

---

## File Structure

- Modify `web/src/styles/global.css`: Replace decorative glass-heavy styling with workbench layout primitives, compact rows, dense metrics, responsive collapse rules, and restrained dark mode.
- Modify `web/src/App.vue`: Adjust app shell copy/classes and sidebar step presentation while preserving route/store behavior.
- Modify `web/src/views/UploadView.vue`: Move upload page toward compact summary plus file review rows.
- Modify `web/src/views/PreviewView.vue`: Keep source switching and actions, but rely on dense candidate rows and side-by-side preview layout.
- Modify `web/src/views/ResultView.vue`: Put result metrics and per-file review ahead of success messaging.
- Modify `web/src/components/FileUploader.vue`: Reduce visual dominance and add concise operational copy.
- Modify `web/src/components/PairList.vue`: Make pair rows compact and queue-like.
- Modify `web/src/components/CandidateCard.vue`: Retain component API, but restyle and structure it as a candidate row.
- Modify `web/src/components/DiffViewer.vue`: Make write preview compact.
- Modify `web/src/components/ProgressPanel.vue`: Make events compact enough for the shell.
- Add `web/src/test/web-workbench-ui.test.ts`: Component tests for upload summary, preview candidate rows, and result review rows using the mock client.
- Modify `.gitignore`: Ignore `.superpowers/` visual companion artifacts.

## Task 1: Add Failing Workbench UI Tests

**Files:**
- Create: `web/src/test/web-workbench-ui.test.ts`

- [ ] **Step 1: Write component tests before implementation**

```ts
import { createPinia, setActivePinia } from 'pinia';
import { describe, expect, it, beforeEach, vi } from 'vitest';
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
```

- [ ] **Step 2: Run tests to verify red state**

Run: `npm run test -- web/src/test/web-workbench-ui.test.ts`

Expected: FAIL because the new `data-testid` workbench markers and dense candidate rows do not exist yet.

## Task 2: Implement Workbench Markup

**Files:**
- Modify: `web/src/App.vue`
- Modify: `web/src/views/UploadView.vue`
- Modify: `web/src/views/PreviewView.vue`
- Modify: `web/src/views/ResultView.vue`
- Modify: `web/src/components/FileUploader.vue`
- Modify: `web/src/components/PairList.vue`
- Modify: `web/src/components/CandidateCard.vue`
- Modify: `web/src/components/DiffViewer.vue`
- Modify: `web/src/components/ProgressPanel.vue`

- [ ] **Step 1: Update App shell markup**

Keep the route, theme, and step behavior. Add stable shell classes for a compact workbench sidebar and keep the workflow navigation.

- [ ] **Step 2: Update upload markup**

Add `data-testid="upload-workbench-summary"`, `data-testid="upload-file-review"`, and `data-testid="upload-pairing-context"`. Keep the file upload event and `startPreview` action unchanged.

- [ ] **Step 3: Update preview markup**

Add `data-testid="source-switcher"` to the source tabs container. Ensure `CandidateCard` root exposes `data-testid="candidate-row"`. Wrap `DiffViewer` in a region with `data-testid="write-preview-context"`.

- [ ] **Step 4: Update result markup**

Add `data-testid="result-workbench-summary"` to metrics and `data-testid="result-file-review"` to per-file review rows. Keep download URL helpers unchanged.

- [ ] **Step 5: Run the new UI tests**

Run: `npm run test -- web/src/test/web-workbench-ui.test.ts`

Expected: PASS.

## Task 3: Apply Restrained Workbench Styling

**Files:**
- Modify: `web/src/styles/global.css`
- Modify scoped styles only if a component already owns them, such as `web/src/components/ThemeToggle.vue`.

- [ ] **Step 1: Replace decorative app background**

Remove ambient glow balls and reduce body background to neutral surface variables.

- [ ] **Step 2: Add workbench grid classes**

Define compact `.app-shell`, `.sidebar`, `.main-area`, `.workbench-grid`, `.workbench-primary`, `.workbench-context`, `.compact-list`, `.candidate-card` row styling, and responsive collapse rules.

- [ ] **Step 3: Reduce motion**

Remove shimmer animation and large hover translations. Keep simple border/background state changes.

- [ ] **Step 4: Verify tests still pass**

Run: `npm run test`

Expected: PASS for all Vitest tests.

## Task 4: Build Verification

**Files:**
- No code changes expected unless TypeScript or build errors expose an issue.

- [ ] **Step 1: Run production build**

Run: `npm run build`

Expected: `vue-tsc --noEmit && vite build` exits with code 0.

- [ ] **Step 2: Inspect git diff**

Run: `git diff --stat`

Expected: Diff is limited to design/plan docs, `.gitignore`, and web UI/test files.

## Self-Review Notes

- The plan covers every spec requirement except visual screenshot verification; this repository currently has no browser automation setup, so build plus component tests are the practical verification baseline.
- No backend files are planned for modification.
- No new behavior depends on new API fields.
