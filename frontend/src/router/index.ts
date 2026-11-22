import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import type { Permission } from '@/constants'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    group?: string
    permission?: Permission
    adminOnly?: boolean
    public?: boolean
    hideInMenu?: boolean
  }
}

export const appRoutes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, hideInMenu: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    redirect: '/documents',
    children: [
      {
        path: 'ask',
        name: 'ask',
        component: () => import('@/views/AskView.vue'),
        meta: { title: '知识问答', group: '知识资产', permission: 'document:read' },
      },
      {
        path: 'documents',
        name: 'documents',
        component: () => import('@/views/DocumentsView.vue'),
        meta: { title: '文档库', group: '知识资产', permission: 'document:read' },
      },
      {
        path: 'documents/upload',
        name: 'document-upload',
        component: () => import('@/views/UploadView.vue'),
        meta: {
          title: '上传文档',
          group: '知识资产',
          permission: 'document:write',
          hideInMenu: true,
        },
      },
      {
        path: 'documents/:id',
        name: 'document-detail',
        component: () => import('@/views/DocumentDetailView.vue'),
        meta: {
          title: '文档详情',
          group: '知识资产',
          permission: 'document:read',
          hideInMenu: true,
        },
      },
      {
        path: 'parse/tasks',
        name: 'parse-tasks',
        component: () => import('@/views/ParseTasksView.vue'),
        meta: { title: '解析任务', group: '知识资产', permission: 'document:read' },
      },
      {
        path: 'parse/strategies',
        name: 'parse-strategies',
        component: () => import('@/views/ParseStrategiesView.vue'),
        meta: { title: '解析策略', group: '知识资产', permission: 'document:read' },
      },
      {
        path: 'ontology',
        name: 'ontology',
        component: () => import('@/views/OntologyView.vue'),
        meta: { title: '本体管理', group: '知识治理', permission: 'ontology:read' },
      },
      {
        path: 'review',
        name: 'review',
        component: () => import('@/views/ReviewView.vue'),
        meta: { title: '候选审核', group: '知识治理', permission: 'graph:read' },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('@/views/GraphView.vue'),
        meta: { title: '图谱浏览', group: '知识治理', permission: 'graph:read' },
      },
      {
        path: 'reviews',
        name: 'reviews',
        component: () => import('@/views/ReviewsView.vue'),
        meta: { title: '审批工作台', group: '流程治理', permission: 'document:read' },
      },
      {
        path: 'review-flows',
        name: 'review-flows',
        component: () => import('@/views/ReviewFlowsView.vue'),
        meta: { title: '审批流配置', group: '流程治理', adminOnly: true },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/UsersView.vue'),
        meta: { title: '用户管理', group: '系统', permission: 'user:manage' },
      },
      {
        path: '403',
        name: 'forbidden',
        component: () => import('@/views/ForbiddenView.vue'),
        meta: { hideInMenu: true, title: '无权访问' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/documents' },
]

const router = createRouter({
  history: createWebHistory(),
  routes: appRoutes,
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true

  const auth = useAuthStore()
  if (!auth.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (!auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      auth.clear()
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  if (to.meta.adminOnly && !auth.isAdmin) {
    return { path: '/403' }
  }
  if (to.meta.permission && !auth.has(to.meta.permission)) {
    return { path: '/403' }
  }
  return true
})

export default router
