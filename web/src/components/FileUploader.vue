<script setup lang="ts">
import { ref } from 'vue';
import { FolderUp, UploadCloud } from 'lucide-vue-next';
import { NButton, NIcon } from 'naive-ui';

const emit = defineEmits<{ files: [files: File[]] }>();

const input = ref<HTMLInputElement | null>(null);
const dragging = ref(false);

function openPicker() {
  input.value?.click();
}

function emitFiles(fileList: FileList | null) {
  if (!fileList?.length) return;
  emit('files', Array.from(fileList));
  if (input.value) input.value.value = '';
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  emitFiles(event.dataTransfer?.files ?? null);
}
</script>

<template>
  <div
    class="upload-zone"
    :class="{ dragging }"
    role="button"
    tabindex="0"
    @click="openPicker"
    @keydown.enter.prevent="openPicker"
    @keydown.space.prevent="openPicker"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <input ref="input" class="hidden-input" type="file" multiple @change="emitFiles(($event.target as HTMLInputElement).files)" />
    <div class="upload-content">
      <div class="upload-icon">
        <NIcon :component="UploadCloud" size="30" />
      </div>
      <h2 class="upload-title">拖入音频与 TTML 文件</h2>
      <p class="upload-text">前端会按同名规则模拟配对，支持 TTML-only 预览路径。</p>
      <NButton type="primary" strong secondary style="margin-top: 16px" @click.stop="openPicker">
        <template #icon>
          <NIcon :component="FolderUp" />
        </template>
        选择文件
      </NButton>
    </div>
  </div>
</template>
