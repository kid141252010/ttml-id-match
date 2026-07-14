<script setup lang="ts">
import { CheckCircle2, Circle, Clock3, Disc3, MapPin, Sparkles } from 'lucide-vue-next';
import { NIcon, NTag } from 'naive-ui';

import type { CandidateBase } from '@/api/types';

const props = defineProps<{
  candidate: CandidateBase;
  selected: boolean;
}>();

const emit = defineEmits<{ toggle: [candidateId: string] }>();

function duration(ms?: number | null) {
  if (!ms) return '-';
  const seconds = Math.round(ms / 1000);
  const minute = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, '0');
  return `${minute}:${rest}`;
}

const fieldLabels: Record<string, string> = {
  title: '标题',
  artist: '艺术家',
  album: '专辑',
  isrc: 'ISRC',
  duration: '时长',
  release_date: '发行日期',
};

const relationLabels: Record<string, string> = {
  exact: '精确匹配',
  normalized: '规范化匹配',
  partial: '部分匹配',
  contains: '包含匹配',
  conflict: '不一致',
  missing: '候选缺失',
  not_provided: '原文件未提供',
  close: '接近',
  compatible: '兼容',
};

function evidenceLabel(field: string, relation: string) {
  return `${fieldLabels[field] ?? field}${relationLabels[relation] ?? relation}`;
}
</script>

<template>
  <article
    class="candidate-card candidate-row"
    :class="{ selected }"
    data-testid="candidate-row"
    role="button"
    tabindex="0"
    @click="emit('toggle', candidate.id)"
    @keydown.enter.prevent="emit('toggle', candidate.id)"
  >
    <div class="candidate-select">
      <NIcon :component="selected ? CheckCircle2 : Circle" :color="selected ? 'var(--app-accent)' : 'var(--app-text-muted)'" size="22" />
    </div>

    <div class="candidate-rank" aria-label="候选排名">
      <span>#{{ candidate.rank }}</span>
    </div>

    <div class="candidate-main">
      <div class="candidate-heading">
        <h3 class="candidate-title">{{ candidate.title || '未命名曲目' }}</h3>
        <NTag v-if="candidate.recommended" size="small" :bordered="false" class="best-tag">
          <template #icon><NIcon :component="Sparkles" size="10" /></template>
          推荐
        </NTag>
      </div>
      <div class="candidate-artists">{{ candidate.artists.join(' / ') || '未知艺术家' }}</div>
      <div class="candidate-meta">
        <div><NIcon :component="Disc3" /> <span>{{ candidate.album || '未知专辑' }}</span></div>
        <div><NIcon :component="MapPin" /> <span>{{ candidate.group || candidate.identifiers?.market || '全局候选' }}</span></div>
        <div><NIcon :component="Clock3" /> <span>{{ duration(candidate.duration_ms) }} · {{ candidate.release_date || '-' }}</span></div>
      </div>
      <div v-if="candidate.evidence?.length" class="candidate-evidence" aria-label="匹配依据">
        <NTag
          v-for="item in candidate.evidence ?? []"
          :key="`${item.field}-${item.relation}-${item.actual ?? ''}`"
          size="small"
          :type="item.relation === 'conflict' || item.relation === 'missing' ? 'warning' : 'success'"
          :bordered="false"
        >
          {{ evidenceLabel(item.field, item.relation) }}
        </NTag>
      </div>
    </div>

    <NTag size="small" round :bordered="false" class="candidate-id mono-text">{{ candidate.id }}</NTag>
  </article>
</template>
