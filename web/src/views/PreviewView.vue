<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { CheckCheck, Download, RotateCcw } from 'lucide-vue-next';
import { NButton, NEmpty, NIcon, NTag } from 'naive-ui';

import CandidateCard from '@/components/CandidateCard.vue';
import DiffViewer from '@/components/DiffViewer.vue';
import PairList from '@/components/PairList.vue';
import { useSessionStore } from '@/stores/session';
import type { PreviewResult, SelectionPayload } from '@/api/types';

const store = useSessionStore();
const router = useRouter();
const activeSource = ref<keyof Omit<SelectionPayload, 'pair_id'>>('apple_music');

const sourceOptions: Array<{ key: keyof Omit<SelectionPayload, 'pair_id'>; label: string }> = [
  { key: 'apple_music', label: 'Apple Music' },
  { key: 'qq_music', label: 'QQ 音乐' },
  { key: 'ncm_music', label: '网易云' },
  { key: 'spotify', label: 'Spotify' },
];

const preview = computed(() => store.selectedPreview);
const sourcePreview = computed(() => preview.value?.[activeSource.value] ?? null);
const selectedIds = computed(() => {
  if (!preview.value) return [];
  return store.selections[preview.value.pair_id]?.[activeSource.value] ?? [];
});

function sourceCount(result: PreviewResult, key: keyof Omit<SelectionPayload, 'pair_id'>) {
  return result[key].candidates.length;
}

async function apply() {
  await store.applySelections();
  await router.push('/result');
}
</script>

<template>
  <NEmpty v-if="store.previewResults.length === 0" description="还没有预览结果">
    <template #extra>
      <NButton @click="router.push('/upload')">返回上传</NButton>
    </template>
  </NEmpty>

  <div v-else class="preview-grid">
    <PairList :pairs="store.pairs" :selected-id="store.selectedPairId" selectable @select="store.selectPair" />

    <div class="stack">
      <section class="panel">
        <div class="panel-header">
          <h2 class="panel-title">候选来源</h2>
          <NTag size="small" round>已选 {{ store.selectionCount }}</NTag>
        </div>
        <div class="panel-body">
          <div class="source-tabs">
            <button
              v-for="source in sourceOptions"
              :key="source.key"
              class="source-tab"
              :class="{ active: activeSource === source.key }"
              @click="activeSource = source.key"
            >
              {{ source.label }} · {{ preview ? sourceCount(preview, source.key) : 0 }}
            </button>
          </div>

          <div v-if="sourcePreview" class="candidate-grid">
            <CandidateCard
              v-for="candidate in sourcePreview.candidates"
              :key="candidate.id"
              :candidate="candidate"
              :selected="selectedIds.includes(candidate.id)"
              @toggle="preview && store.toggleCandidate(preview.pair_id, activeSource, $event)"
            />
          </div>
          <NEmpty v-else description="无候选" />

          <div class="action-row">
            <NButton secondary @click="store.acceptBestForAll">
              <template #icon><NIcon :component="CheckCheck" /></template>
              全部接受最佳
            </NButton>
            <NButton secondary @click="store.previewAll">
              <template #icon><NIcon :component="RotateCcw" /></template>
              重新预览
            </NButton>
            <NButton type="primary" :disabled="!store.canApply" :loading="store.loading" @click="apply">
              <template #icon><NIcon :component="Download" /></template>
              写入并生成结果
            </NButton>
          </div>
        </div>
      </section>

      <DiffViewer v-if="preview" :changes="preview.changes" />
    </div>
  </div>
</template>
