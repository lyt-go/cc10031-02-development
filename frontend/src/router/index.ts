import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SpikeReviewView from '@/views/SpikeReviewView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/spike-review', name: 'spike-review', component: SpikeReviewView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
