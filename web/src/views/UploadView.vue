<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { ArrowRight, FileAudio, FileText } from 'lucide-vue-next';
import { NButton, NIcon, NTag } from 'naive-ui';

import FileUploader from '@/components/FileUploader.vue';
import PairList from '@/components/PairList.vue';
import { useSessionStore } from '@/stores/session';

const store = useSessionStore();
const router = useRouter();

const ttmlCount = computed(() => store.files.filter((file) => file.kind === 'ttml').length);
const audioCount = computed(() => store.files.filter((file) => file.kind === 'audio').length);

async function onFiles(files: File[]) {
  await store.uploadFiles(files);
}

async function startPreview() {
  await store.previewAll();
  await router.push('/preview');
}
</script>

<template>
  <div class="view-grid">
    <div class="stack">
      <FileUploader @files="onFiles" />

      <section class="panel">
        <div class="panel-header">
          <h2 class="panel-title">上传文件</h2>
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
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
          <div class="metric-row">
            <div class="metric">
              <span class="metric-value">{{ store.files.length }}</span>
              <span class="metric-label">文件</span>
            </div>
            <div class="metric">
              <span class="metric-value">{{ store.pairs.length }}</span>
              <span class="metric-label">TTML 组</span>
            </div>
            <div class="metric">
              <span class="metric-value">{{ store.pairs.filter((pair) => pair.status === 'paired').length }}</span>
              <span class="metric-label">音频配对</span>
            </div>
          </div>

          <div v-if="store.files.length" class="file-list" style="margin-top: 14px">
            <div v-for="file in store.files" :key="file.name" class="file-row">
              <div class="file-main">
                <span class="file-name">{{ file.name }}</span>
                <NTag size="small" round>{{ file.kind }}</NTag>
              </div>
              <span class="muted">{{ file.size }} bytes</span>
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

    <PairList :pairs="store.pairs" :selected-id="store.selectedPairId" @select="store.selectPair" />
  </div>
</template>
