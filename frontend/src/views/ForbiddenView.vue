<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="card card-pad forbidden">
    <div class="code">403</div>
    <h2>当前角色无权访问该页面</h2>
    <p class="muted">菜单项和按钮都会按角色权限渲染；如需权限请联系管理员调整角色。</p>
    <el-button type="primary" @click="router.push('/documents')">返回文档库</el-button>
  </div>
</template>

<style scoped>
.forbidden {
  max-width: 520px;
  margin: 8vh auto;
  text-align: center;
  padding: 48px 32px;
}

.code {
  font-size: 56px;
  font-weight: 700;
  letter-spacing: -2px;
  background: linear-gradient(135deg, #4f46e5, #8b5cf6);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

h2 {
  margin: 8px 0 6px;
  font-size: 18px;
}

p {
  margin: 0 0 22px;
  font-size: 13px;
}
</style>
