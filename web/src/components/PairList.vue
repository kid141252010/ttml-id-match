<script setup lang="ts">
import { computed } from 'vue';
import { FileAudio, FileText, Link2, Unlink } from 'lucide-vue-next';
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
}));
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2 class="panel-title">配对状态</h2>
      <div style="display: flex; gap: 8px; flex-wrap: wrap">
        <NTag size="small" type="success" round>{{ counts.paired }} 已配对</NTag>
        <NTag size="small" type="warning" round>{{ counts.ttmlOnly }} TTML-only</NTag>
      </div>
    </div>
    <div class="panel-body">
      <NEmpty v-if="pairs.length === 0" description="尚未上传文件" />
      <div v-else class="pair-list">
        <div
          v-for="pair in pairs"
          :key="pair.id"
          class="pair-row"
          :class="{ active: pair.id === selectedId, selectable }"
          @click="selectable && emit('select', pair.id)"
        >
          <div class="pair-main">
            <div class="pair-name" style="font-size: 15px; font-weight: 700; letter-spacing: -0.01em;">{{ pair.ttml }}</div>
            <NTag :type="pair.status === 'paired' ? 'success' : 'warning'" size="small" round strong>
              <template #icon>
                <NIcon :component="pair.status === 'paired' ? Link2 : Unlink" />
              </template>
              {{ pair.status === 'paired' ? '已配对' : '仅歌词' }}
            </NTag>
          </div>
          <div class="muted" style="display: flex; flex-direction: column; gap: 6px; font-size: 12px; margin-top: 4px;">
            <div style="display: flex; align-items: center; gap: 8px; background: rgba(0, 0, 0, 0.02); padding: 4px 8px; border-radius: 6px; border: 1px solid var(--app-border);">
              <NIcon :component="FileText" color="var(--app-accent)" />
              <span style="font-family: 'JetBrains Mono', monospace; word-break: break-all;">{{ pair.ttml || '-' }}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px; background: rgba(0, 0, 0, 0.02); padding: 4px 8px; border-radius: 6px; border: 1px solid var(--app-border);">
              <NIcon :component="FileAudio" :color="pair.status === 'paired' ? '#6366f1' : 'var(--app-text-muted)'" />
              <span :style="{ fontFamily: '\'JetBrains Mono\', monospace', wordBreak: 'break-all', color: pair.status === 'paired' ? 'var(--app-text)' : 'var(--app-text-muted)' }">
                {{ pair.audio || '未匹配音频文件' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
