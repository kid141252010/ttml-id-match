<script setup lang="ts">
import { computed } from 'vue';
import { NEmpty, NTag } from 'naive-ui';

import type { ChangeSet } from '@/api/types';

const props = defineProps<{ changes: ChangeSet }>();

const groups = computed(() => [
  { key: 'added', label: '新增', type: 'success' as const, data: props.changes.added },
  { key: 'replaced', label: '替换', type: 'warning' as const, data: props.changes.replaced },
  { key: 'skipped', label: '跳过', type: 'default' as const, data: props.changes.skipped },
]);

function entries(data: Record<string, string[]>) {
  return Object.entries(data).flatMap(([key, values]) => values.map((value) => ({ key, value })));
}
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">写入预览</h2>
        <p class="panel-kicker">当前选择将写入的元数据变化。</p>
      </div>
      <NTag size="small" round>dry-run</NTag>
    </div>
    <div class="panel-body">
      <div class="change-grid">
        <div v-for="group in groups" :key="group.key" class="change-column">
          <h3 class="change-heading">
            <NTag :type="group.type" size="small" round>{{ group.label }}</NTag>
          </h3>
          <NEmpty v-if="entries(group.data).length === 0" size="small" description="无" />
          <div 
            v-for="item in entries(group.data)" 
            :key="`${group.key}-${item.key}-${item.value}`" 
            class="change-item"
            :class="`change-${group.key}`"
          >
            <strong>{{ item.key }}</strong>
            <div class="mono-text">{{ item.value }}</div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
