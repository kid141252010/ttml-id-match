<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';
import { DatabaseZap } from 'lucide-vue-next';
import { darkTheme, NConfigProvider, NIcon, NMessageProvider } from 'naive-ui';
import type { GlobalThemeOverrides } from 'naive-ui';

import ProgressPanel from '@/components/ProgressPanel.vue';
import ThemeToggle from '@/components/ThemeToggle.vue';
import { useSessionStore } from '@/stores/session';
import type { WorkflowStep } from '@/api/types';

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#0d9488',
    primaryColorHover: '#0f766e',
    primaryColorPressed: '#115e59',
    primaryColorSuppl: '#14b8a6',
    successColor: '#16a34a',
    successColorHover: '#15803d',
    successColorPressed: '#166534',
    successColorSuppl: '#22c55e',
    warningColor: '#d97706',
    warningColorHover: '#b45309',
    warningColorPressed: '#92400e',
    warningColorSuppl: '#f59e0b',
    errorColor: '#dc2626',
    errorColorHover: '#b91c1c',
    errorColorPressed: '#991b1b',
    errorColorSuppl: '#ef4444',
    borderRadius: '8px',
    borderRadiusSmall: '8px',
  },
  Button: {
    borderRadiusMedium: '8px',
    borderRadiusLarge: '8px',
    borderRadiusSmall: '8px',
    fontWeightStrong: '600',
  },
  Tag: {
    borderRadius: '8px',
  },
  Progress: {
    borderRadius: '4px',
  }
};

const store = useSessionStore();
const route = useRoute();
const router = useRouter();

const themeMode = ref<'light' | 'dark' | 'auto'>('auto');
const isDark = ref(false);

const updateTheme = () => {
  if (themeMode.value === 'auto') {
    isDark.value = window.matchMedia('(prefers-color-scheme: dark)').matches;
  } else {
    isDark.value = themeMode.value === 'dark';
  }
  document.documentElement.classList.toggle('dark', isDark.value);
};

const setThemeMode = (mode: 'light' | 'dark' | 'auto') => {
  themeMode.value = mode;
  localStorage.setItem('ttml-id-match-theme-mode', mode);
  updateTheme();
};

const steps: Array<{ key: WorkflowStep; path: string; title: string; caption: string }> = [
  { key: 'upload', path: '/upload', title: '上传', caption: '文件识别与配对' },
  { key: 'preview', path: '/preview', title: '预览', caption: '候选与 diff' },
  { key: 'result', path: '/result', title: '下载', caption: '结果汇总' },
];

const pageCopy = computed(() => {
  if (route.name === 'preview') return { title: '候选工作台', subtitle: '逐组核对来源候选、写入预览和批量动作。' };
  if (route.name === 'result') return { title: '写入复查', subtitle: '检查成功、失败和跳过项，并下载处理后的 TTML。' };
  return { title: '上传与配对', subtitle: '添加 TTML 与音频文件，先确认识别和配对质量。' };
});

onMounted(() => {
  const saved = localStorage.getItem('ttml-id-match-theme-mode') as 'light' | 'dark' | 'auto' | null;
  themeMode.value = saved || 'auto';
  updateTheme();

  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
  mediaQuery.addEventListener('change', () => {
    if (themeMode.value === 'auto') {
      updateTheme();
    }
  });
});

watch(
  () => route.name,
  (name) => {
    if (name === 'preview') store.setStep('preview');
    else if (name === 'result') store.setStep('result');
    else store.setStep('upload');
  },
  { immediate: true },
);

function go(step: (typeof steps)[number]) {
  router.push(step.path);
}
</script>

<template>
  <NConfigProvider :theme="isDark ? darkTheme : null" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <div class="app-shell">
        <aside class="sidebar">
          <div class="brand">
            <div class="brand-mark">
              <NIcon :component="DatabaseZap" size="23" />
            </div>
            <div>
              <h1 class="brand-title">TTML ID Match</h1>
              <p class="brand-subtitle">Batch metadata workbench</p>
            </div>
          </div>

          <nav class="step-list" aria-label="流程步骤">
            <button v-for="(step, index) in steps" :key="step.key" class="step-item" :class="{ active: store.currentStep === step.key }" @click="go(step)">
              <span class="step-number">{{ index + 1 }}</span>
              <span>
                <span class="step-title">{{ step.title }}</span>
                <span class="step-caption">{{ step.caption }}</span>
              </span>
            </button>
          </nav>

          <div class="sidebar-footer">
            <ThemeToggle :theme-mode="themeMode" @update:theme-mode="setThemeMode" />
            <ProgressPanel :events="store.progressEvents" />
          </div>
        </aside>

        <main class="main-area">
          <header class="topbar">
            <div>
              <h2 class="page-title">{{ pageCopy.title }}</h2>
              <p class="page-subtitle">{{ pageCopy.subtitle }}</p>
            </div>
          </header>
          <RouterView />
        </main>
      </div>
    </NMessageProvider>
  </NConfigProvider>
</template>
