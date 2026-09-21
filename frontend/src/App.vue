<template>
  <div class="app-root">
    <header class="top-bar">
      <h1>🏭 数字孪生工厂产线实时监控系统</h1>
      <div class="status-row">
        <span class="ws-dot" :class="{on: store.connected}"></span>
        <span>{{ store.connected ? '实时连接中' : '连接断开' }}</span>
        <span class="prod-count">今日产量: {{ store.data?.production || 0 }}</span>

        <!-- 登录 / 当前账号 -->
        <span class="auth-box">
          <template v-if="auth.isLoggedIn">
            <span class="auth-user">{{ auth.user?.display_name }}</span>
            <el-tag size="small" :type="auth.user?.configured ? 'success' : 'danger'">
              {{ auth.user?.configured ? auth.user?.role : auth.user?.role + '·权限缺失' }}
            </el-tag>
            <el-button size="small" text @click="doLogout">退出登录</el-button>
          </template>
          <template v-else>
            <el-select v-model="loginName" size="small" placeholder="选择账号登录" style="width:170px">
              <el-option v-for="u in DEMO_ACCOUNTS" :key="u.username"
                         :label="u.label" :value="u.username" />
            </el-select>
            <el-button size="small" type="primary" :loading="loginBusy" @click="doLogin">登录</el-button>
          </template>
        </span>
      </div>
    </header>
    <div class="main-grid">
      <div class="scene-col"><FactoryScene /></div>
      <div class="panel-col">
        <DeviceList />
        <AnomalyList />
      </div>
    </div>
    <div class="dashboard-row">
      <OEEChart />
      <TrendPanel />
      <FaultPie />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import FactoryScene from './components/FactoryScene.vue'
import DeviceList from './components/DeviceList.vue'
import AnomalyList from './components/AnomalyList.vue'
import OEEChart from './components/OEEChart.vue'
import TrendPanel from './components/TrendPanel.vue'
import FaultPie from './components/FaultPie.vue'
import { useFactoryStore } from './store/factory'
import { useAuthStore } from './store/auth'
import { ApiError } from './api'

const store = useFactoryStore()
const auth = useAuthStore()

// 演示账号，与后端 USERS 一致
const DEMO_ACCOUNTS = [
  { username: 'alice', label: 'alice · 管理员' },
  { username: 'bob', label: 'bob · 运维工程师' },
  { username: 'carol', label: 'carol · 访客（只读）' },
  { username: 'dave', label: 'dave · 外包（权限未配置）' },
]
const loginName = ref('')
const loginBusy = ref(false)
let syncTimer: ReturnType<typeof setInterval> | null = null

async function doLogin() {
  if (!loginName.value) { ElMessage.warning('请选择要登录的账号'); return }
  loginBusy.value = true
  try {
    await auth.login(loginName.value)
    await store.fetchAnomalies()
    startSync()
    ElMessage.success(`已登录：${auth.user?.display_name}`)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '登录失败，请重试')
  } finally {
    loginBusy.value = false
  }
}

// 已登录期间定时同步处置信息（实时推送负责告警条数/标签，这里只补责任归属）
function startSync() {
  if (syncTimer) return
  syncTimer = setInterval(() => {
    if (auth.isLoggedIn) store.fetchAnomalies().catch(() => {})
  }, 5000)
}

async function doLogout() {
  await auth.logout()
  ElMessage.info('已退出登录')
}

onMounted(async () => {
  store.connect()
  // 持久化会话：页面刷新后仍拉取告警处置信息
  if (auth.isLoggedIn) {
    try {
      await auth.refreshUsers()
      await store.fetchAnomalies()
      startSync()
    } catch {
      await auth.logout()
    }
  }
})
onUnmounted(() => {
  if (syncTimer) clearInterval(syncTimer)
  store.disconnect()
})
</script>

<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0a1628;color:#e0e6ed;overflow-x:hidden}
.app-root{min-height:100vh}
.top-bar{display:flex;justify-content:space-between;align-items:center;padding:12px 24px;background:linear-gradient(90deg,#0d2137,#1a3a5c);border-bottom:1px solid #1e3a5f}
.top-bar h1{font-size:1.2rem;color:#64b5f6}
.status-row{display:flex;gap:20px;align-items:center;font-size:13px;color:#94a3b8}
.ws-dot{width:10px;height:10px;border-radius:50%;background:#ef4444}
.ws-dot.on{background:#22c55e;box-shadow:0 0 8px #22c55e}
.prod-count{color:#fbbf24;font-weight:600}
.auth-box{display:flex;align-items:center;gap:8px;margin-left:12px;padding-left:12px;border-left:1px solid #1e3a5f}
.auth-user{color:#e0e6ed;font-size:12px}
.main-grid{display:grid;grid-template-columns:1fr 360px;gap:12px;padding:12px 24px;min-height:55vh}
.scene-col{background:#0d1b2a;border-radius:12px;border:1px solid #1e3a5f;overflow:hidden}
.panel-col{display:flex;flex-direction:column;gap:12px;overflow-y:auto;max-height:55vh}
.dashboard-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:0 24px 16px}
</style>