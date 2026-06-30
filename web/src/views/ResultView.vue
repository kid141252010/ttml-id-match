<script setup lang="ts">
import { useRouter } from 'vue-router';
import { Archive, FileDown, RotateCcw, FileText } from 'lucide-vue-next';
import { NButton, NEmpty, NIcon, NResult, NTag } from 'naive-ui';

import { downloadAllUrl, downloadFileUrl } from '@/api/client';
import { useSessionStore } from '@/stores/session';

const store = useSessionStore();
const router = useRouter();
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
          <p class="panel-kicker">优先检查失败和跳过项，再下载全部或单个 TTML。</p>
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
            <span class="metric-label">跳过</span>
          </div>
        </div>

        <div class="result-list compact-list" data-testid="result-file-review">
          <div v-for="file in store.resultSummary.files" :key="file.pair_id" class="result-row" :class="`status-${file.status}`">
            <div class="result-main">
              <div class="file-identity">
                <NIcon :component="FileText" color="var(--app-accent)" size="18" />
                <span class="result-name mono-text">{{ file.ttml }}</span>
              </div>
              <NTag :type="file.status === 'success' ? 'success' : file.status === 'skipped' ? 'warning' : 'error'" size="small" round strong>
                {{ file.status === 'success' ? '成功' : file.status === 'skipped' ? '跳过' : '失败' }}
              </NTag>
            </div>

            <div class="metadata-summary">
              <span class="muted summary-label">写入元数据</span>
              <template v-if="file.status === 'success' && file.metadata_written.length">
                <NTag v-for="meta in file.metadata_written" :key="meta" size="small" :bordered="false" class="metadata-chip mono-text">
                  {{ meta }}
                </NTag>
              </template>
              <span v-else-if="file.error" class="error-text">{{ file.error }}</span>
              <span v-else class="muted">-</span>
            </div>

            <div class="row-actions">
              <NButton size="small" tag="a" :href="store.sessionId ? downloadFileUrl(store.sessionId, file.ttml) : undefined" :disabled="file.status !== 'success'" secondary strong>
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
              <NButton secondary @click="router.push('/upload')">
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
