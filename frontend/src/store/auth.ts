import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
import { parseError } from '../api'
import type { User, RolePermission } from '../types'

const STORAGE_KEY = 'factory_current_user'

export const useAuthStore = defineStore('auth', () => {
  const users = ref<User[]>([])
  const rolePermissions = ref<Record<string, RolePermission>>({})
  const userId = ref<string>(localStorage.getItem(STORAGE_KEY) || 'admin')
  const loadError = ref('')

  const currentUser = computed(() => users.value.find((u) => u.id === userId.value) || null)
  const currentRole = computed(() => currentUser.value?.role || '')
  const permission = computed<RolePermission | undefined>(() => rolePermissions.value[currentRole.value])
  // 角色未在后端权限配置中登记 => 权限配置缺失
  const permMissing = computed(() => !!currentUser.value && permission.value === undefined)
  const isAdmin = computed(() => permission.value?.can_handle === 'any')
  const canHandleAssigned = computed(() => permission.value?.can_handle === 'assigned')

  async function loadUsers() {
    try {
      // 首次拉取时本地可能还没有有效账号, 兜底带一个已知账号头
      const res = await axios.get('/api/auth/users', {
        headers: { 'X-User-Id': userId.value || 'admin' },
      })
      users.value = res.data.users
      rolePermissions.value = res.data.role_permissions
      // 本地保存的账号已失效时回退到第一个账号
      if (!users.value.find((u) => u.id === userId.value)) userId.value = users.value[0]?.id || ''
    } catch (e) {
      loadError.value = parseError(e).reasons.join('；')
    }
  }

  function switchUser(id: string) {
    userId.value = id
    localStorage.setItem(STORAGE_KEY, id)
  }

  // 当前账号是否有权编辑某条告警
  function canEdit(assignee?: string | null) {
    if (permMissing.value) return false
    if (isAdmin.value) return true
    if (canHandleAssigned.value) return assignee === userId.value
    return false
  }

  // 无权编辑时给出明确原因(只读弹窗提示用)
  function denyReason(assignee?: string | null, assigneeName?: string | null): string {
    if (!currentUser.value) return '未识别到登录账号'
    const name = currentUser.value.name
    if (permMissing.value) {
      return `当前账号角色 '${currentRole.value}' 未配置告警处置权限（权限配置缺失），请联系管理员补充配置。`
    }
    if (permission.value?.can_handle === 'none') {
      return `当前账号 '${name}' 为${permission.value.label}，仅可只读查看，不能修改处置说明与状态。`
    }
    if (canHandleAssigned.value && assignee !== userId.value) {
      return assignee
        ? `该告警处置人为 '${assigneeName || assignee}'，当前账号 '${name}' 既不是处置人也不是管理员，只能查看。`
        : `该告警尚未指派处置人，当前账号 '${name}' 无权处置，请联系管理员指派。`
    }
    return ''
  }

  return {
    users, rolePermissions, userId, loadError,
    currentUser, currentRole, permission, permMissing, isAdmin, canHandleAssigned,
    loadUsers, switchUser, canEdit, denyReason,
  }
})
