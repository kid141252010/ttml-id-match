<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { ArrowRight, FileAudio, FileText } from 'lucide-vue-next';
import { NAlert, NButton, NIcon, NTag } from 'naive-ui';

import FileUploader from '@/components/FileUploader.vue';
import PairList from '@/components/PairList.vue';
import { useSessionStore } from '@/stores/session';

const store = useSessionStore();
const router = useRouter();

const ttmlCount = computed(() => store.files.filter((file) => file.kind === 'ttml').length);
const audioCount = computed(() => store.files.filter((file) => file.kind === 'audio').length);
const pairedCount = computed(() => store.pairs.filter((pair) => pair.status === 'paired').length);
const ttmlOnlyCount = computed(() => store.pairs.filter((pair) => pair.status === 'ttml_only').length);

async function onFiles(files: File[]) {
  await store.uploadFiles(files);
}

async function startPreview() {
  await store.previewAll();
  await router.push('/preview');
}

function issueMessage(code: string, ttmlPath: string) {
  if (code === 'duplicate_ttml_key') return `${ttmlPath} 与另一 TTML 规范化后重名`;
  return `${ttmlPath} 的音频配对存在歧义`;
}
</script>

<template>
  <div class="workbench-grid upload-workbench">
    <div class="workbench-primary stack">
      <FileUploader @files="onFiles" />

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">文件识别</h2>
            <p class="panel-kicker">检查文件类型、大小和配对状态，再进入候选预览。</p>
          </div>
          <div class="status-tags">
            <NTag type="success" size="small" round>
              <template #icon><NIcon :component="FileText" /></template>
              {{ ttmlCount }} TTML
            </NTag>
            <NTag type="info" size="small" round>
              <template #icon><NIcon :component="FileAudio" /></template>
              {{ audioCount }} 音频
            </NTag>
          </div>
        </div>
        <div class="panel-body">
          <NAlert
            v-for="issue in store.pairingIssues"
            :key="`${issue.code}-${issue.pair_id}`"
            type="error"
            :title="issueMessage(issue.code, issue.ttml_path)"
            class="pairing-issue"
          />
          <div class="metric-row" data-testid="upload-workbench-summary">
            <div class="metric">
              <span class="metric-value">{{ store.files.length }}</span>
              <span class="metric-label">文件</span>
            </div>
            <div class="metric">
              <span class="metric-value">{{ store.pairs.length }}</span>
              <span class="metric-label">TTML 组</span>
            </div>
            <div class="metric">
              <span class="metric-value">{{ pairedCount }}</span>
              <span class="metric-label">音频配对</span>
            </div>
            <div class="metric warning-metric">
              <span class="metric-value">{{ ttmlOnlyCount }}</span>
              <span class="metric-label">仅歌词</span>
            </div>
          </div>

          <div v-if="store.files.length" class="file-list compact-list" data-testid="upload-file-review">
            <div v-for="file in store.files" :key="file.name" class="file-row">
              <div class="file-main">
                <div class="file-identity">
                  <NIcon :component="file.kind === 'ttml' ? FileText : FileAudio" :color="file.kind === 'ttml' ? 'var(--app-accent)' : 'var(--app-info)'" size="18" />
                  <span class="file-name mono-text">{{ file.name }}</span>
                </div>
                <div class="row-meta">
                  <NTag :type="file.kind === 'ttml' ? 'success' : 'info'" size="small" round strong>{{ file.kind.toUpperCase() }}</NTag>
                  <span class="muted mono-text">
                    {{ file.size > 1024 * 1024 ? (file.size / (1024 * 1024)).toFixed(1) + ' MB' : file.size > 1024 ? Math.round(file.size / 1024) + ' KB' : file.size + ' B' }}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div class="action-row">
            <NButton type="primary" :disabled="!store.canPreview" :loading="store.loading" @click="startPreview">
              <template #icon><NIcon :component="ArrowRight" /></template>
              开始预览
            </NButton>
          </div>
        </div>
      </section>
    </div>

    <div class="workbench-context" data-testid="upload-pairing-context">
      <PairList :pairs="store.pairs" :selected-id="store.selectedPairId" @select="store.selectPair" />
    </div>
  </div>
</template>
