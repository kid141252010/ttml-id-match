import { createRouter, createWebHistory } from 'vue-router';

import PreviewView from '@/views/PreviewView.vue';
import ResultView from '@/views/ResultView.vue';
import UploadView from '@/views/UploadView.vue';

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/upload' },
    { path: '/upload', name: 'upload', component: UploadView },
    { path: '/preview', name: 'preview', component: PreviewView },
    { path: '/result', name: 'result', component: ResultView },
  ],
});
