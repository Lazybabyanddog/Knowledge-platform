<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import type { Permission } from '@/constants'
import { ROLE_LABELS } from '@/constants'
import { appRoutes } from '@/router'
import { useAuthStore } from '@/stores/auth'

interface NavItem {
  path: string
  title: string
  group: string
  icon: string
  permission?: Permission
}

const ICONS: Record<string, string> = {
  ask: 'ChatDotRound',
  documents: 'Files',
  'parse-tasks': 'DataAnalysis',
  'parse-strategies': 'SetUp',
  ontology: 'Share',
  review: 'Checked',
  graph: 'Connection',
  reviews: 'Stamp',
  'review-flows': 'Operation',
  users: 'User',
}

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const menu = computed<NavItem[]>(() => {
  const children = appRoutes.find((item) => item.path === '/')?.children ?? []
  return children
    .filter((item) => !item.meta?.hideInMenu && item.name)
    .filter((item) => !item.meta?.adminOnly || auth.isAdmin)
    .filter((item) => !item.meta?.permission || auth.has(item.meta.permission))
    .map((item) => ({
      path: `/${String(item.path)}`,
      title: item.meta?.title ?? String(item.name),
      group: item.meta?.group ?? '其它',
      icon: ICONS[String(item.name)] ?? 'Grid',
      permission: item.meta?.permission,
    }))
})

const groups = computed(() => {
  const map = new Map<string, NavItem[]>()
  for (const item of menu.value) {
    const list = map.get(item.group) ?? []
    list.push(item)
    map.set(item.group, list)
  }
  return [...map.entries()].map(([name, items]) => ({ name, items }))
})

const currentTitle = computed(() => route.meta.title ?? '企业知识库')

const roleText = computed(() =>
  auth.roles.map((role) => ROLE_LABELS[role] ?? role).join(' · ') || '—',
)

async function onCommand(command: string): Promise<void> {
  if (command === 'logout') {
    await ElMessageBox.confirm('退出后需要重新登录，确认退出？', '退出登录', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await auth.logout()
    await router.push('/login')
  }
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <div class="brand-name">知识中台</div>
          <div class="brand-desc">Enterprise Knowledge</div>
        </div>
      </div>

      <nav class="nav">
        <div v-for="group in groups" :key="group.name">
          <div class="nav-section">{{ group.name }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            :class="{ active: route.path === item.path || route.path.startsWith(`${item.path}/`) }"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </router-link>
        </div>
      </nav>

      <div class="sidebar-foot">
        <div class="dot" />
        <span>API 已接入 · v1</span>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="topbar-title">{{ currentTitle }}</div>
        <el-dropdown trigger="click" @command="onCommand">
          <div class="user-chip">
            <el-avatar :size="30" class="avatar">
              {{ auth.user?.display_name?.slice(0, 1) ?? 'U' }}
            </el-avatar>
            <div class="user-meta">
              <div class="user-name">{{ auth.user?.display_name ?? '未登录' }}</div>
              <div class="user-role">{{ roleText }}</div>
            </div>
            <el-icon class="chev"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>
                {{ auth.user?.username }}
              </el-dropdown-item>
              <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>

      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  display: flex;
  flex-direction: column;
  width: 228px;
  flex: 0 0 228px;
  background: radial-gradient(120% 80% at 0% 0%, #1e2438 0%, #12151f 55%, #0d0f16 100%);
  color: #fff;
  position: sticky;
  top: 0;
  height: 100vh;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 18px 12px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.brand-name {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0.02em;
}

.brand-desc {
  font-size: 10.5px;
  color: #8b93a7;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.nav {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 12px;
}

.sidebar-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 14px 18px;
  border-top: 1px solid rgb(255 255 255 / 7%);
  color: #7d8598;
  font-size: 11.5px;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 8px #34d399;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 24px;
  background: rgb(255 255 255 / 82%);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}

.topbar-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-soft);
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 5px 10px 5px 6px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.16s ease;
}

.user-chip:hover {
  background: #f2f3f7;
}

.avatar {
  background: linear-gradient(135deg, #4f46e5, #7c3aed);
  font-size: 12px;
}

.user-meta {
  line-height: 1.2;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
}

.user-role {
  font-size: 11px;
  color: var(--muted);
}

.chev {
  color: var(--muted);
  font-size: 12px;
}

.content {
  padding: 22px 24px 40px;
  flex: 1;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.fade-leave-to {
  opacity: 0;
}
</style>
