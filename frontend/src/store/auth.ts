import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { CurrentUser } from '@/types'
import { api } from '@/api'

const TOKEN_KEY = 'dt_factory_token'
const USER_KEY = 'dt_factory_user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<CurrentUser | null>(
    (() => { try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null } })()
  )
  const users = ref<CurrentUser[]>([])

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.permissions.includes('anomaly:assign_any') ?? false)
  const canHandle = computed(() => user.value?.permissions.includes('anomaly:handle') ?? false)

  function setSession(t: string, u: CurrentUser) {
    token.value = t
    user.value = u
    localStorage.setItem(TOKEN_KEY, t)
    localStorage.setItem(USER_KEY, JSON.stringify(u))
  }

  async function login(username: string) {
    const data = await api<{ token: string; user: CurrentUser }>('/api/auth/login', {
      method: 'POST', body: { username },
    })
    setSession(data.token, data.user)
    await refreshUsers()
  }

  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }) } catch { /* 即使后端调用失败也清本地会话 */ }
    token.value = null
    user.value = null
    users.value = []
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  async function refreshUsers() {
    if (!token.value) return
    const data = await api<{ users: CurrentUser[] }>('/api/users')
    users.value = data.users
  }

  /**
   * 判断当前账号是否可以编辑某条告警的处置说明/状态。
   * 规则与后端一致：管理员可以改任意告警；其他人只能改处置人是自己的告警。
   * （仅有查看权限或权限配置缺失的账号一律不可编辑。）
   */
  function canEditAnomaly(assignee?: string | null): boolean {
    if (!user.value || !user.value.configured) return false
    if (isAdmin.value) return true
    return canHandle.value && !!assignee && assignee === user.value.username
  }

  /** 非管理员只能把尚未认领的告警处置人指定为自己 */
  function canClaim(assignee?: string | null): boolean {
    if (!user.value || !user.value.configured || !canHandle.value) return false
    if (isAdmin.value) return true
    return !assignee
  }

  return {
    token, user, users, isLoggedIn, isAdmin, canHandle,
    login, logout, refreshUsers, setSession, canEditAnomaly, canClaim,
  }
})
