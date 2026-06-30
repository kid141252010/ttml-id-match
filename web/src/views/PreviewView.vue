<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { CheckCheck, Download, RotateCcw, Music, Headphones, Disc, Radio } from 'lucide-vue-next';
import { NButton, NEmpty, NIcon, NTag } from 'naive-ui';

import CandidateCard from '@/components/CandidateCard.vue';
import DiffViewer from '@/components/DiffViewer.vue';
import PairList from '@/components/PairList.vue';
import { useSessionStore } from '@/stores/session';
import type { PreviewResult, SelectionPayload } from '@/api/types';

const store = useSessionStore();
const router = useRouter();
const activeSource = ref<keyof Omit<SelectionPayload, 'pair_id'>>('apple_music');

const sourceOptions: Array<{ key: keyof Omit<SelectionPayload, 'pair_id'>; label: string; icon: any }> = [
  { key: 'apple_music', label: 'Apple Music', icon: Music },
  { key: 'qq_music', label: 'QQ 音乐', icon: Headphones },
  { key: 'ncm_music', label: '网易云', icon: Disc },
  { key: 'spotify', label: 'Spotify', icon: Radio },
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

  <div v-else class="workbench-grid preview-workbench">
    <div class="workbench-queue">
      <PairList :pairs="store.pairs" :selected-id="store.selectedPairId" selectable @select="store.selectPair" />
    </div>

    <div class="workbench-primary stack">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">候选核对</h2>
            <p class="panel-kicker">按来源快速比较候选，确认后再写入 TTML。</p>
          </div>
          <NTag size="small" round>已选 {{ store.selectionCount }}</NTag>
        </div>
        <div class="panel-body">
          <div class="source-tabs" data-testid="source-switcher">
            <button
              v-for="source in sourceOptions"
              :key="source.key"
              class="source-tab"
              :class="{ active: activeSource === source.key }"
              @click="activeSource = source.key"
            >
              <NIcon :component="source.icon" size="15" />
              <span>{{ source.label }}</span>
              <span class="source-count mono-text">
                {{ preview ? sourceCount(preview, source.key) : 0 }}
              </span>
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

      <div v-if="preview" class="workbench-context" data-testid="write-preview-context">
        <DiffViewer :changes="preview.changes" />
      </div>
    </div>
  </div>
</template>
