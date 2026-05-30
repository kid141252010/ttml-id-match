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
            <div class="pair-name">{{ pair.ttml }}</div>
            <NTag :type="pair.status === 'paired' ? 'success' : 'warning'" size="small" round>
              <template #icon>
                <NIcon :component="pair.status === 'paired' ? Link2 : Unlink" />
              </template>
              {{ pair.status === 'paired' ? '音频已匹配' : '仅歌词' }}
            </NTag>
          </div>
          <div class="muted" style="display: grid; gap: 4px">
            <span><NIcon :component="FileText" /> {{ pair.ttml || '-' }}</span>
            <span><NIcon :component="FileAudio" /> {{ pair.audio || '未匹配同名音频' }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
