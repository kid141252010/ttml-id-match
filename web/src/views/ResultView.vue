<script setup lang="ts">
import { useRouter } from 'vue-router';
import { Archive, FileDown, RotateCcw, FileText } from 'lucide-vue-next';
import { NButton, NEmpty, NIcon, NResult, NTag } from 'naive-ui';

import { downloadAllUrl, downloadFileUrl } from '@/api/client';
import { useSessionStore } from '@/stores/session';

const store = useSessionStore();
const router = useRouter();

async function newSession() {
  await store.resetSession();
  await router.push('/upload');
}
</script>

<template>
  <NEmpty v-if="!store.resultSummary" description="还没有写入结果">
    <template #extra>
      <NButton @click="router.push('/preview')">返回预览</NButton>
    </template>
  </NEmpty>

  <div v-else class="workbench-grid result-workbench">
    <section class="panel workbench-primary">
      <div class="panel-header">
        <div>
          <h2 class="panel-title">写入复查</h2>
          <p class="panel-kicker">已写入与未变化文件都可下载；失败项保留结构化错误。</p>
        </div>
        <NTag size="small" type="success" round>{{ store.resultSummary.files.length }} 文件</NTag>
      </div>
      <div class="panel-body">
        <div class="metric-row" data-testid="result-workbench-summary">
          <div class="metric">
            <span class="metric-value">{{ store.resultSummary.succeeded }}</span>
            <span class="metric-label">成功</span>
          </div>
          <div class="metric danger-metric">
            <span class="metric-value">{{ store.resultSummary.failed }}</span>
            <span class="metric-label">失败</span>
          </div>
          <div class="metric warning-metric">
            <span class="metric-value">{{ store.resultSummary.skipped }}</span>
            <span class="metric-label">未变化</span>
          </div>
        </div>

        <div class="result-list compact-list" data-testid="result-file-review">
          <div v-for="file in store.resultSummary.files" :key="file.pair_id" class="result-row" :class="`status-${file.status}`">
            <div class="result-main">
              <div class="file-identity">
                <NIcon :component="FileText" color="var(--app-accent)" size="18" />
                <span class="result-name mono-text">{{ file.ttml }}</span>
              </div>
              <NTag :type="file.status === 'applied' ? 'success' : file.status === 'unchanged' ? 'warning' : 'error'" size="small" round strong>
                {{ file.status === 'applied' ? '已写入' : file.status === 'unchanged' ? '未变化' : '失败' }}
              </NTag>
            </div>

            <div class="metadata-summary">
              <span class="muted summary-label">输出校验</span>
              <span v-if="file.output_sha256" class="mono-text">{{ file.output_sha256.slice(0, 16) }}</span>
              <NTag v-if="file.backup" size="small" :bordered="false" class="metadata-chip mono-text">
                {{ file.backup }}
              </NTag>
              <span v-if="file.error" class="error-text">{{ file.error.message }}</span>
            </div>

            <div class="row-actions">
              <NButton size="small" tag="a" :href="store.sessionId ? downloadFileUrl(store.sessionId, file.ttml) : undefined" :disabled="file.status === 'failed'" secondary strong>
                <template #icon><NIcon :component="FileDown" /></template>
                单文件下载
              </NButton>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="panel workbench-context">
      <div class="panel-body">
        <NResult status="success" title="写入流程已完成" description="可下载单个 TTML，也可以打包下载全部结果。">
          <template #footer>
            <div class="action-row context-actions">
              <NButton type="primary" tag="a" :href="store.sessionId ? downloadAllUrl(store.sessionId) : undefined">
                <template #icon><NIcon :component="Archive" /></template>
                打包下载 ZIP
              </NButton>
              <NButton secondary @click="newSession">
                <template #icon><NIcon :component="RotateCcw" /></template>
                新会话
              </NButton>
            </div>
          </template>
        </NResult>
      </div>
    </section>
  </div>
</template>
