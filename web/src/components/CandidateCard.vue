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
  <article class="candidate-card" :class="{ selected }" role="button" tabindex="0" @click="emit('toggle', candidate.id)" @keydown.enter.prevent="emit('toggle', candidate.id)">
    <div class="candidate-top">
      <div style="flex: 1; min-width: 0;">
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px;">
          <h3 class="candidate-title" style="margin: 0; font-size: 16px; font-weight: 800; word-break: break-all;">{{ candidate.title }}</h3>
          <NTag v-if="isHighScore" size="small" :bordered="false" style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); color: white; font-weight: 700; height: 20px; padding: 0 6px; box-shadow: 0 4px 10px rgba(217, 119, 6, 0.25);">
            <template #icon><NIcon :component="Sparkles" size="10" /></template>
            最佳
          </NTag>
        </div>
        <div class="candidate-meta" style="font-weight: 600; font-size: 13px; color: var(--app-text-muted);">{{ candidate.artists.join(' / ') }}</div>
      </div>
      <NIcon :component="selected ? CheckCircle2 : Circle" :color="selected ? 'var(--app-accent)' : 'var(--app-text-muted)'" size="24" style="transition: transform 0.2s ease;" :style="{ transform: selected ? 'scale(1.1)' : 'scale(1)' }" />
    </div>
    
    <div class="candidate-meta" style="margin: 12px 0;">
      <div><NIcon :component="Disc3" /> <span style="word-break: break-all;">{{ candidate.album }}</span></div>
      <div><NIcon :component="MapPin" /> <span>{{ candidate.region || '全局候选' }}</span></div>
      <div><NIcon :component="Clock3" /> <span>{{ duration(candidate.duration_ms) }} · {{ candidate.release_date || '-' }}</span></div>
    </div>
    
    <div style="margin-bottom: 12px;">
      <div class="candidate-meta" style="margin-bottom: 6px; display: flex; justify-content: space-between; font-weight: 600;">
        <span>匹配度</span>
        <span :style="{ color: isHighScore ? 'var(--app-accent)' : 'var(--app-text-muted)' }">{{ candidate.score }}%</span>
      </div>
      <NProgress type="line" :percentage="candidate.score" :height="6" :show-indicator="false" status="success" />
    </div>
    
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <NTag size="small" round :bordered="false" style="background: rgba(0, 0, 0, 0.03); font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 500;">
        {{ candidate.id }}
      </NTag>
    </div>
  </article>
</template>
