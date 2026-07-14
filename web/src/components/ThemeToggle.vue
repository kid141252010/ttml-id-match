<script setup lang="ts">
import { Monitor, Moon, Sun } from 'lucide-vue-next';
import { NIcon } from 'naive-ui';

defineProps<{ themeMode: 'light' | 'dark' | 'auto' }>();
const emit = defineEmits<{ 'update:themeMode': [value: 'light' | 'dark' | 'auto'] }>();
</script>

<template>
  <div class="theme-selector-segmented" aria-label="主题切换">
    <button
      class="segmented-btn"
      :class="{ active: themeMode === 'light' }"
      @click="emit('update:themeMode', 'light')"
      title="浅色模式"
    >
      <NIcon :component="Sun" size="16" />
      <span class="btn-text">浅色</span>
    </button>
    <button
      class="segmented-btn"
      :class="{ active: themeMode === 'auto' }"
      @click="emit('update:themeMode', 'auto')"
      title="跟随浏览器"
    >
      <NIcon :component="Monitor" size="16" />
      <span class="btn-text">自动</span>
    </button>
    <button
      class="segmented-btn"
      :class="{ active: themeMode === 'dark' }"
      @click="emit('update:themeMode', 'dark')"
      title="深色模式"
    >
      <NIcon :component="Moon" size="16" />
      <span class="btn-text">深色</span>
    </button>
  </div>
</template>

<style scoped>
.theme-selector-segmented {
  display: flex;
  background: var(--app-surface-muted);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 3px;
  gap: 2px;
  width: 100%;
}

.segmented-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: none;
  background: transparent;
  padding: 7px 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--app-text-muted);
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  outline: none;
}

.segmented-btn:hover {
  color: var(--app-text);
  background: rgba(120, 120, 120, 0.05);
}

.segmented-btn.active {
  color: var(--app-accent-strong);
  background: var(--app-surface);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
  font-weight: 600;
}

:global(.dark) .segmented-btn.active {
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
}

@media (max-width: 1080px) {
  .btn-text {
    display: none;
  }
  .segmented-btn {
    padding: 8px 0;
  }
}
</style>
