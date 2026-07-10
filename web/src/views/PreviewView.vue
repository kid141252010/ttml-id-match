<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { CheckCheck, Download, RotateCcw } from 'lucide-vue-next';
import { NAlert, NButton, NEmpty, NIcon, NTag } from 'naive-ui';

import CandidateCard from '@/components/CandidateCard.vue';
import DiffViewer from '@/components/DiffViewer.vue';
import PairList from '@/components/PairList.vue';
import { orderedSourceDescriptors } from '@/domain/sourceCatalog';
import { useSessionStore } from '@/stores/session';
import type { SourceKey } from '@/api/types';

const store = useSessionStore();
const router = useRouter();
const activeSource = ref<SourceKey | null>(null);

const preview = computed(() => store.selectedPreview);
const sourceOptions = computed(() => orderedSourceDescriptors(
  Object.keys(preview.value?.sources ?? {}),
));
const sourcePreview = computed(() => (
  preview.value && activeSource.value ? preview.value.sources[activeSource.value] ?? null : null
));
const selectedIds = computed(() => {
  if (!preview.value || !activeSource.value) return [];
  return store.selections[preview.value.pair_id]?.sources[activeSource.value] ?? [];
});

watch(sourceOptions, (options) => {
  if (!options.some((source) => source.key === activeSource.value)) {
    activeSource.value = options[0]?.key ?? null;
  }
}, { immediate: true });

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
            <p class="panel-kicker">来源由服务端动态提供；每次选择都会重新计算写入计划。</p>
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
                {{ preview?.sources[source.key]?.candidates.length ?? 0 }}
              </span>
            </button>
          </div>

          <NAlert
            v-for="warning in sourcePreview?.warnings ?? []"
            :key="warning"
            type="warning"
            :title="warning"
            class="source-warning"
          />

          <div v-if="sourcePreview?.candidates.length" class="candidate-grid">
            <CandidateCard
              v-for="candidate in sourcePreview.candidates"
              :key="candidate.id"
              :candidate="candidate"
              :selected="selectedIds.includes(candidate.id)"
              @toggle="preview && activeSource && store.toggleCandidate(preview.pair_id, activeSource, $event)"
            />
          </div>
          <NEmpty v-else description="当前来源无候选" />

          <div class="action-row">
            <NButton secondary @click="store.acceptBestForAll">
              <template #icon><NIcon :component="CheckCheck" /></template>
              全部接受推荐
            </NButton>
            <NButton secondary :disabled="store.loading" @click="store.previewAll">
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
        <DiffViewer :plan="store.selectedChangePlan" />
      </div>
    </div>
  </div>
</template>
