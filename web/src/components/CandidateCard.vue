<script setup lang="ts">
import { computed } from 'vue';
import { CheckCircle2, Circle, Clock3, Disc3, MapPin, Sparkles } from 'lucide-vue-next';
import { NIcon, NProgress, NTag } from 'naive-ui';

import type { CandidateBase } from '@/api/types';

const props = defineProps<{
  candidate: CandidateBase;
  selected: boolean;
}>();

const emit = defineEmits<{ toggle: [candidateId: string] }>();

const isHighScore = computed(() => props.candidate.score >= 90);

function duration(ms?: number) {
  if (!ms) return '-';
  const seconds = Math.round(ms / 1000);
  const minute = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, '0');
  return `${minute}:${rest}`;
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

    <div class="candidate-score">
      <span>{{ candidate.score }}</span>
      <NProgress type="line" :percentage="candidate.score" :height="5" :show-indicator="false" status="success" />
    </div>

    <div class="candidate-main">
      <div class="candidate-heading">
        <h3 class="candidate-title">{{ candidate.title }}</h3>
        <NTag v-if="isHighScore" size="small" :bordered="false" class="best-tag">
          <template #icon><NIcon :component="Sparkles" size="10" /></template>
          最佳
        </NTag>
      </div>
      <div class="candidate-artists">{{ candidate.artists.join(' / ') }}</div>
      <div class="candidate-meta">
        <div><NIcon :component="Disc3" /> <span>{{ candidate.album }}</span></div>
        <div><NIcon :component="MapPin" /> <span>{{ candidate.region || '全局候选' }}</span></div>
        <div><NIcon :component="Clock3" /> <span>{{ duration(candidate.duration_ms) }} · {{ candidate.release_date || '-' }}</span></div>
      </div>
    </div>

    <NTag size="small" round :bordered="false" class="candidate-id mono-text">{{ candidate.id }}</NTag>
  </article>
</template>
