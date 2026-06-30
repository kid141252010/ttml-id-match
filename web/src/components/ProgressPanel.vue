<script setup lang="ts">
import { NBadge, NEmpty, NTag } from 'naive-ui';

import type { ProgressEvent } from '@/api/types';

withDefaults(defineProps<{ events?: ProgressEvent[] }>(), {
  events: () => [],
});

function tagType(level: ProgressEvent['level']) {
  if (level === 'success') return 'success';
  if (level === 'warning') return 'warning';
  if (level === 'error') return 'error';
  return 'info';
}

function label(level: ProgressEvent['level']) {
  return { info: '信息', success: '完成', warning: '警告', error: '错误' }[level];
}
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2 class="panel-title">实时进度</h2>
      <NBadge :value="events.length" type="info" />
    </div>
    <div class="panel-body">
      <NEmpty v-if="events.length === 0" size="small" description="等待操作" />
      <div v-else class="event-list">
        <div v-for="event in events" :key="event.id" class="event-row" :class="event.level">
          <div class="event-main">
            <NTag :type="tagType(event.level)" size="small" round strong>{{ label(event.level) }}</NTag>
            <span class="muted mono-text event-time">{{ event.at }}</span>
          </div>
          <div class="event-message mono-text">{{ event.message }}</div>
        </div>
      </div>
    </div>
  </section>
</template>
