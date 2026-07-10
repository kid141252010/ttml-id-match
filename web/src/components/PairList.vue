<script setup lang="ts">
import { computed } from 'vue';
import { FileAudio, FileText, Link2, TriangleAlert, Unlink } from 'lucide-vue-next';
import { NEmpty, NIcon, NTag } from 'naive-ui';

import type { FilePair } from '@/api/types';

const props = withDefaults(
  defineProps<{
    pairs: FilePair[];
    selectedId?: string | null;
    selectable?: boolean;
  }>(),
  { selectedId: null, selectable: false },
);

const emit = defineEmits<{ select: [pairId: string] }>();

const counts = computed(() => ({
  paired: props.pairs.filter((pair) => pair.status === 'paired').length,
  ttmlOnly: props.pairs.filter((pair) => pair.status === 'ttml_only').length,
  ambiguous: props.pairs.filter((pair) => pair.status === 'ambiguous').length,
}));

function statusLabel(status: FilePair['status']) {
  if (status === 'paired') return '已配对';
  if (status === 'ambiguous') return '音频冲突';
  return '仅歌词';
}

function statusType(status: FilePair['status']): 'success' | 'warning' | 'error' {
  if (status === 'paired') return 'success';
  if (status === 'ambiguous') return 'error';
  return 'warning';
}

function audioLabel(pair: FilePair) {
  if (pair.status === 'paired') return pair.audio || '-';
  if (pair.status === 'ambiguous') return pair.audio_candidates.join(' / ');
  return '未匹配音频文件';
}
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">配对队列</h2>
        <p class="panel-kicker">按 TTML 组核对音频配对。</p>
      </div>
      <div class="status-tags">
        <NTag size="small" type="success" round>{{ counts.paired }} 已配对</NTag>
        <NTag v-if="counts.ttmlOnly" size="small" type="warning" round>{{ counts.ttmlOnly }} TTML-only</NTag>
        <NTag v-if="counts.ambiguous" size="small" type="error" round>{{ counts.ambiguous }} 音频冲突</NTag>
      </div>
    </div>
    <div class="panel-body">
      <NEmpty v-if="pairs.length === 0" description="尚未上传文件" />
      <div v-else class="pair-list">
        <div
          v-for="pair in pairs"
          :key="pair.id"
          class="pair-row"
          :class="[{ active: pair.id === selectedId, selectable }, `status-${pair.status}`]"
          @click="selectable && emit('select', pair.id)"
        >
          <div class="pair-main">
            <div class="pair-name">{{ pair.ttml }}</div>
            <NTag :type="statusType(pair.status)" size="small" round strong>
              <template #icon>
                <NIcon :component="pair.status === 'paired' ? Link2 : pair.status === 'ambiguous' ? TriangleAlert : Unlink" />
              </template>
              {{ statusLabel(pair.status) }}
            </NTag>
          </div>
          <div class="pair-paths muted">
            <div class="pair-path">
              <NIcon :component="FileText" color="var(--app-accent)" />
              <span class="mono-text">{{ pair.ttml || '-' }}</span>
            </div>
            <div class="pair-path">
              <NIcon :component="FileAudio" :color="pair.status === 'paired' ? 'var(--app-info)' : 'var(--app-text-muted)'" />
              <span class="mono-text" :class="{ muted: pair.status !== 'paired' }">
                {{ audioLabel(pair) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
