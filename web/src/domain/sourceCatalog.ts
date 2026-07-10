import type { Component } from 'vue';
import { Database, Disc3, Headphones, Music, Radio } from 'lucide-vue-next';

import type { SourceKey } from '@/api/types';

export interface SourceDescriptor {
  key: SourceKey;
  label: string;
  icon: Component;
  order: number;
}

const catalog: Record<string, Omit<SourceDescriptor, 'key'>> = {
  apple_music: { label: 'Apple Music', icon: Music, order: 10 },
  qq_music: { label: 'QQ 音乐', icon: Headphones, order: 20 },
  ncm_music: { label: '网易云', icon: Disc3, order: 30 },
  spotify: { label: 'Spotify', icon: Radio, order: 40 },
};

export function sourceDescriptor(key: SourceKey): SourceDescriptor {
  const known = catalog[key];
  return known
    ? { key, ...known }
    : { key, label: humanize(key), icon: Database, order: 1000 };
}

export function orderedSourceDescriptors(keys: SourceKey[]): SourceDescriptor[] {
  return [...new Set(keys)]
    .map(sourceDescriptor)
    .sort((left, right) => left.order - right.order || left.label.localeCompare(right.label));
}

function humanize(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(' ') || 'Unknown source';
}
