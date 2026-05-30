<script setup lang="ts">
import { CheckCircle2, Circle, Clock3, Disc3, MapPin } from 'lucide-vue-next';
import { NIcon, NProgress, NTag } from 'naive-ui';

import type { CandidateBase } from '@/api/types';

const props = defineProps<{
  candidate: CandidateBase;
  selected: boolean;
}>();

const emit = defineEmits<{ toggle: [candidateId: string] }>();

function duration(ms?: number) {
  if (!ms) return '-';
  const seconds = Math.round(ms / 1000);
  const minute = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, '0');
  return `${minute}:${rest}`;
}
</script>

<template>
  <article class="candidate-card" :class="{ selected }" role="button" tabindex="0" @click="emit('toggle', candidate.id)" @keydown.enter.prevent="emit('toggle', candidate.id)">
    <div class="candidate-top">
      <div>
        <h3 class="candidate-title">{{ candidate.title }}</h3>
        <div class="candidate-meta">{{ candidate.artists.join(' / ') }}</div>
      </div>
      <NIcon :component="selected ? CheckCircle2 : Circle" :color="selected ? 'var(--app-accent)' : 'var(--app-text-muted)'" size="22" />
    </div>
    <div class="candidate-meta">
      <div><NIcon :component="Disc3" /> {{ candidate.album }}</div>
      <div><NIcon :component="MapPin" /> {{ candidate.region || '全局候选' }}</div>
      <div><NIcon :component="Clock3" /> {{ duration(candidate.duration_ms) }} · {{ candidate.release_date || '-' }}</div>
    </div>
    <div>
      <div class="candidate-meta" style="margin-bottom: 4px">匹配可信度 {{ candidate.score }}</div>
      <NProgress type="line" :percentage="candidate.score" :height="6" :show-indicator="false" status="success" />
    </div>
    <NTag size="small" round>{{ candidate.id }}</NTag>
  </article>
</template>
