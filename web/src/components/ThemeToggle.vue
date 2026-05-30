<script setup lang="ts">
import { computed } from 'vue';
import { Moon, Sun } from 'lucide-vue-next';
import { NButton, NIcon, NSwitch } from 'naive-ui';

const props = defineProps<{ dark: boolean }>();
const emit = defineEmits<{ 'update:dark': [value: boolean] }>();

const checked = computed({
  get: () => props.dark,
  set: (value: boolean) => emit('update:dark', value),
});
</script>

<template>
  <div class="theme-toggle" aria-label="主题切换">
    <NIcon :component="Sun" size="17" />
    <NSwitch v-model:value="checked" size="small" />
    <NIcon :component="Moon" size="17" />
  </div>
</template>

<style scoped>
.theme-toggle {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--app-text-muted);
}
</style>
