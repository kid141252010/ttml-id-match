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
      <div class="upload-copy">
        <h2 class="upload-title">{{ dragging ? '释放鼠标以上传' : '添加 TTML 与音频文件' }}</h2>
        <p class="upload-text">{{ dragging ? '系统将立即解析文件并建立配对关系' : '同名文件会自动配对，缺音频的 TTML 仍可进入预览。' }}</p>
      </div>
      <NButton type="primary" strong secondary @click.stop="openPicker">
        <template #icon>
          <NIcon :component="FolderUp" />
        </template>
        选择文件
      </NButton>
    </div>
  </div>
</template>
