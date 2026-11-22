<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { errorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: 'admin', password: 'Admin@12345' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    ElMessage.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/documents'
    await router.replace(redirect)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="hero">
      <div class="hero-inner">
        <div class="badge">Enterprise Knowledge Platform</div>
        <h1>把散落的文档<br />变成可审核的知识</h1>
        <p class="lede">
          上传 → 解析 → 抽取 → 审核 → 图谱。全链路留痕，本体驱动约束，候选实体先审后入库。
        </p>

        <div class="flow">
          <div class="flow-item"><span>01</span>文档接入</div>
          <div class="flow-item"><span>02</span>结构解析</div>
          <div class="flow-item"><span>03</span>知识抽取</div>
          <div class="flow-item"><span>04</span>人工审核</div>
        </div>

        <div class="hero-foot">
          <div class="kv"><strong>200 MB</strong><span>单文件上限</span></div>
          <div class="kv"><strong>3 级</strong><span>子图检索深度</span></div>
          <div class="kv"><strong>7 天</strong><span>合并可撤销窗口</span></div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-inner">
        <div class="brand">
          <div class="brand-mark">AI</div>
          <div>
            <div class="brand-name">知识中台</div>
            <div class="brand-desc">Sign in to continue</div>
          </div>
        </div>

        <h2>账号登录</h2>
        <p class="hint">使用管理员分配的账号登录，角色决定可见菜单与操作权限。</p>

        <el-form ref="formRef" :model="form" :rules="rules" size="large" @submit.prevent="submit">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名" :prefix-icon="'User'" />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="密码"
              :prefix-icon="'Lock'"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button type="primary" class="submit" :loading="loading" @click="submit">
            登录
          </el-button>
        </el-form>

        <div class="demo">
          <span>种子账号</span>
          <code>admin / Admin@12345</code>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  min-height: 100vh;
  background: #fff;
}

.hero {
  position: relative;
  display: flex;
  align-items: center;
  padding: 64px;
  color: #eef0ff;
  background: radial-gradient(120% 90% at 10% 0%, #2b2f6b 0%, #191c31 45%, #0c0e16 100%);
  overflow: hidden;
}

.hero::after {
  content: '';
  position: absolute;
  inset: auto -20% -40% -10%;
  height: 60%;
  background: radial-gradient(60% 60% at 50% 50%, rgb(99 102 241 / 35%), transparent 70%);
  filter: blur(30px);
}

.hero-inner {
  position: relative;
  z-index: 1;
  max-width: 520px;
}

.badge {
  display: inline-block;
  margin-bottom: 26px;
  padding: 5px 12px;
  border: 1px solid rgb(255 255 255 / 18%);
  border-radius: 999px;
  color: #a5b0d8;
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.hero h1 {
  margin: 0;
  font-size: 40px;
  line-height: 1.24;
  letter-spacing: -0.8px;
  font-weight: 650;
}

.lede {
  margin: 18px 0 30px;
  color: #a8b0c8;
  font-size: 14px;
  line-height: 1.8;
}

.flow {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.flow-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 10px;
  background: rgb(255 255 255 / 4%);
  font-size: 13px;
  color: #d5d9ea;
}

.flow-item span {
  font-family: monospace;
  font-size: 11px;
  color: #8b95bd;
}

.hero-foot {
  display: flex;
  gap: 34px;
  margin-top: 42px;
  padding-top: 22px;
  border-top: 1px solid rgb(255 255 255 / 10%);
}

.kv {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.kv strong {
  font-size: 19px;
  font-weight: 600;
}

.kv span {
  color: #8b95bd;
  font-size: 11.5px;
}

.panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  background: #fbfbfd;
}

.panel-inner {
  width: 100%;
  max-width: 360px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 40px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #4f46e5, #8b5cf6);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.brand-name {
  font-size: 15px;
  font-weight: 650;
}

.brand-desc {
  color: #98a2b3;
  font-size: 10.5px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

h2 {
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 650;
  letter-spacing: -0.3px;
}

.hint {
  margin: 0 0 26px;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.7;
}

.submit {
  width: 100%;
  height: 42px;
  margin-top: 6px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.demo {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 26px;
  padding: 10px 12px;
  border: 1px dashed #dfe1e8;
  border-radius: 9px;
  color: var(--muted);
  font-size: 12px;
}

.demo code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: var(--ink-soft);
}

@media (max-width: 960px) {
  .login-page {
    grid-template-columns: 1fr;
  }

  .hero {
    display: none;
  }
}
</style>
