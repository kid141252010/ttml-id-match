<script setup lang="ts">
import { computed } from 'vue';
import { NEmpty, NTag } from 'naive-ui';

import type { ChangePlanSummary } from '@/api/types';

const props = defineProps<{ plan: ChangePlanSummary | null }>();

const groups = computed(() => [
  { key: 'added', label: '新增', type: 'success' as const, data: props.plan?.metadata.added ?? {} },
  { key: 'replaced', label: '替换', type: 'warning' as const, data: props.plan?.metadata.replaced ?? {} },
  { key: 'skipped', label: '跳过', type: 'default' as const, data: props.plan?.metadata.skipped ?? {} },
]);

const normalizationItems = computed(() => {
  const summary = props.plan?.normalization;
  if (!summary) return [];
  const items: string[] = [];
  if (summary.language_changed) items.push('语言标签已规范化');
  if (summary.body_text_changed) items.push('正文文本已转换');
  if (summary.removed_translations) items.push(`移除 ${summary.removed_translations} 个翻译层`);
  if (summary.removed_transliterations) items.push(`移除 ${summary.removed_transliterations} 个音译层`);
  return items;
});

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
      <NTag size="small" round :type="plan?.changed ? 'warning' : 'default'">
        {{ plan ? '当前选择' : '等待计算' }}
      </NTag>
    </div>
    <div class="panel-body">
      <NEmpty v-if="!plan" size="small" description="正在计算当前选择的写入计划" />
      <template v-else>
        <div class="normalization-summary" data-testid="normalization-summary">
          <h3 class="change-section-title">文本规范化</h3>
          <div v-if="normalizationItems.length" class="normalization-items">
            <NTag v-for="item in normalizationItems" :key="item" size="small" type="info" :bordered="false">
              {{ item }}
            </NTag>
          </div>
          <span v-else class="muted">无语言层变更</span>
        </div>

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

        <div class="final-output">
          <h3 class="change-section-title">完整 TTML</h3>
          <pre class="final-ttml" data-testid="final-ttml">{{ plan.final_text }}</pre>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.final-output {
  margin-top: 20px;
}

.final-ttml {
  max-height: 320px;
  margin: 0;
  overflow: auto;
  padding: 14px;
  border: 1px solid var(--app-border);
  border-radius: 10px;
  background: var(--app-surface-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: "JetBrains Mono", "Cascadia Code", monospace;
  font-size: 12px;
  line-height: 1.55;
}
</style>
