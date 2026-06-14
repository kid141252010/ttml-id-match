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

  <div v-else class="view-grid">
    <section class="panel">
      <div class="panel-body">
        <NResult status="success" title="写入流程已完成" description="可下载单个 TTML，也可以打包下载全部结果。">
          <template #footer>
            <div class="action-row" style="justify-content: center">
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

        <div class="metric-row" style="margin-top: 10px">
          <div class="metric">
            <span class="metric-value">{{ store.resultSummary.succeeded }}</span>
            <span class="metric-label">成功</span>
          </div>
          <div class="metric">
            <span class="metric-value">{{ store.resultSummary.failed }}</span>
            <span class="metric-label">失败</span>
          </div>
          <div class="metric">
            <span class="metric-value">{{ store.resultSummary.skipped }}</span>
            <span class="metric-label">跳过</span>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2 class="panel-title">逐文件结果</h2>
        <NTag size="small" type="success" round>{{ store.resultSummary.files.length }} 文件</NTag>
      </div>
      <div class="panel-body">
        <div class="result-list">
          <div v-for="file in store.resultSummary.files" :key="file.pair_id" class="result-row">
            <div class="result-main">
              <div style="display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1;">
                <NIcon :component="FileText" color="var(--app-accent)" size="18" />
                <span class="result-name" style="word-break: break-all; font-family: 'JetBrains Mono', monospace; font-size: 13px;">{{ file.ttml }}</span>
              </div>
              <NTag :type="file.status === 'success' ? 'success' : 'error'" size="small" round strong>
                {{ file.status === 'success' ? '成功' : '失败' }}
              </NTag>
            </div>
            
            <div style="display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 10px; align-items: center;">
              <span class="muted" style="font-size: 12px; font-weight: 600;">写入元数据:</span>
              <template v-if="file.status === 'success' && file.metadata_written.length">
                <NTag v-for="meta in file.metadata_written" :key="meta" size="small" :bordered="false" style="background: rgba(13, 148, 136, 0.08); color: var(--app-accent); font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600;">
                  {{ meta }}
                </NTag>
              </template>
              <span v-else-if="file.error" style="color: var(--app-danger); font-size: 12px; font-weight: 500;">{{ file.error }}</span>
              <span v-else class="muted" style="font-size: 12px;">-</span>
            </div>

            <div style="display: flex; justify-content: flex-end;">
              <NButton size="small" tag="a" :href="store.sessionId ? downloadFileUrl(store.sessionId, file.ttml) : undefined" :disabled="file.status !== 'success'" secondary strong>
                <template #icon><NIcon :component="FileDown" /></template>
                单文件下载
              </NButton>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
