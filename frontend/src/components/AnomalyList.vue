<template>
  <div class="panel">
    <div class="panel-head">
      <h4>⚠️ 近期告警</h4>
      <el-select
        v-model="auth.userId" size="small" class="user-select"
        @change="onSwitchUser"
      >
        <el-option
          v-for="u in auth.users" :key="u.id" :value="u.id"
          :label="`${u.name}（${roleLabel(u.role)}）`"
        />
      </el-select>
    </div>
    <div v-if="auth.loadError" class="load-error">账号加载失败: {{ auth.loadError }}</div>
    <div v-if="!anomalies.length" class="empty">暂无告警</div>
    <div
      v-for="a in anomalies" :key="a.id ?? `${a.timestamp}`" class="anomaly-row"
      :class="{ clickable: !!a.id }" @click="openDialog(a)"
    >
      <span class="a-time">{{ ts(a.timestamp) }}</span>
      <span v-for="t in a.triggers" :key="t.rule" class="a-tag">{{ t.rule }}: {{ t.value.toFixed(1) }}</span>
      <span class="a-meta">
        <el-tag size="small" :type="statusTagType(a.status)" effect="dark">{{ statusLabel(a.status) }}</el-tag>
        <span class="a-owner" :class="{ unassigned: !a.assignee }">
          👤 {{ a.assignee_name || '未指派' }}
        </span>
      </span>
      <div v-if="a.note" class="a-note">📝 {{ a.note }}</div>
    </div>

    <!-- 处置 / 只读查看弹窗 -->
    <el-dialog
      v-model="dialogVisible" title="告警处置" width="520px"
      :close-on-click-modal="false" append-to-body
    >
      <div v-if="current" class="dlg">
        <div class="dlg-line">
          <span class="a-time">{{ ts(current.timestamp) }}</span>
          <span v-for="t in current.triggers" :key="t.rule" class="a-tag">{{ t.rule }}: {{ t.value.toFixed(1) }}</span>
        </div>

        <!-- 无编辑权限: 顶部说明原因, 内容只读 -->
        <el-alert
          v-if="!editable" type="warning" :closable="false" show-icon
          :title="readonlyTitle" :description="auth.denyReason(current.assignee, current.assignee_name)"
          class="deny-alert"
        />

        <el-form label-width="84px" class="dlg-form" @submit.prevent>
          <el-form-item label="处置人" required>
            <el-select
              v-model="form.assignee" class="full"
              :disabled="!editable || !auth.isAdmin"
              placeholder="请选择处置人"
            >
              <el-option
                v-for="u in engineerOptions" :key="u.id" :value="u.id"
                :label="`${u.name}（${roleLabel(u.role)}）`"
              />
            </el-select>
            <div v-if="editable && !auth.isAdmin" class="field-hint">仅管理员可指派/变更处置人</div>
            <div v-if="editable && auth.isAdmin && !current.assignee" class="field-hint warn">该告警尚未指派处置人，请先选择处置人</div>
          </el-form-item>

          <el-form-item label="处置状态">
            <el-radio-group v-model="form.status" :disabled="!editable">
              <el-radio label="PENDING">待处理</el-radio>
              <el-radio label="PROCESSING">处理中</el-radio>
              <el-radio label="RESOLVED">已解决</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="处置说明">
            <el-input
              v-model="form.note" type="textarea" :rows="3"
              :readonly="!editable" :disabled="!editable"
              placeholder="请填写本次处置的具体说明（可选）"
            />
          </el-form-item>

          <div v-if="current.updated_by" class="updated-hint">
            最近更新: {{ userName(current.updated_by) }} · {{ current.updated_at ? ts(current.updated_at) : '-' }}
          </div>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">{{ editable ? '取消' : '关闭' }}</el-button>
        <el-button v-if="editable" type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useFactoryStore } from '../store/factory'
import { useAuthStore } from '../store/auth'
import { http, parseError } from '../api'
import type { Anomaly, AlertStatus } from '../types'

const store = useFactoryStore()
const auth = useAuthStore()
const anomalies = computed(() => store.data?.anomalies || [])

const ALERT_STATUS: AlertStatus[] = ['PENDING', 'PROCESSING', 'RESOLVED']

const dialogVisible = ref(false)
const saving = ref(false)
const current = ref<Anomaly | null>(null)
const form = reactive<{ assignee: string; status: AlertStatus; note: string }>({
  assignee: '', status: 'PENDING', note: '',
})

const editable = computed(() => current.value ? auth.canEdit(current.value.assignee) : false)
const readonlyTitle = computed(() => auth.permMissing ? '权限配置缺失 — 仅可查看' : '只读查看 — 无权修改')

// 处置人候选: 工程师 + 管理员(管理员也可领单)
const engineerOptions = computed(() =>
  auth.users.filter((u) => {
    const p = auth.rolePermissions[u.role]
    return p?.can_handle === 'assigned' || p?.can_handle === 'any'
  }),
)

function roleLabel(role: string) {
  return auth.rolePermissions[role]?.label || `未配置权限(${role})`
}
function userName(id?: string | null) {
  return auth.users.find((u) => u.id === id)?.name || id || '-'
}
function ts(t: number) { return new Date(t * 1000).toLocaleTimeString() }
function statusLabel(s?: string) {
  return { PENDING: '待处理', PROCESSING: '处理中', RESOLVED: '已解决' }[s || 'PENDING']
}
function statusTagType(s?: string): 'info' | 'warning' | 'success' {
  const m: Record<string, 'info' | 'warning' | 'success'> = {
    PENDING: 'info', PROCESSING: 'warning', RESOLVED: 'success',
  }
  return m[s || 'PENDING'] ?? 'info'
}

function onSwitchUser(id: string) {
  auth.switchUser(id)
  // 切换账号后弹窗权限态即时变化
}

function openDialog(a: Anomaly) {
  if (!a.id) return // 无 id 的异常记录不可处置
  current.value = a
  form.assignee = a.assignee || ''
  form.status = (a.status || 'PENDING') as AlertStatus
  form.note = a.note || ''
  dialogVisible.value = true
}

function localInvalid(): string[] {
  const reasons: string[] = []
  if (!form.assignee) reasons.push('处置人为空：每条告警必须有明确的处置人，请先指派处置人后再保存。')
  if (!ALERT_STATUS.includes(form.status)) reasons.push('处置状态不合法。')
  return reasons
}

async function save() {
  if (!current.value?.id) return
  const invalid = localInvalid()
  if (invalid.length) {
    ElMessage.error({ message: '存在不合格项，未允许保存：' + invalid.join('；'), duration: 4000 })
    return
  }
  saving.value = true
  try {
    const res = await http.put(`/api/anomalies/${current.value.id}/handle`, {
      assignee: auth.isAdmin ? form.assignee : undefined,
      status: form.status,
      note: form.note,
    })
    const updated = res.data.anomaly as Anomaly
    store.patchAnomaly(updated)
    current.value = { ...current.value, ...updated }
    ElMessage.success('处置结果已保存')
    dialogVisible.value = false
  } catch (e) {
    const err = parseError(e)
    const msg = err.reasons.length ? err.reasons.join('；') : err.error
    if (err.status === 403) {
      ElMessage.error({ message: `越权操作被拒绝：${msg}`, duration: 5000 })
    } else if (err.status === 400) {
      ElMessage.error({ message: `未允许保存（不合格项）：${msg}`, duration: 5000 })
    } else {
      ElMessage.error({ message: `${err.error}：${msg}`, duration: 4000 })
    }
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  if (!auth.users.length) auth.loadUsers()
})
</script>

<style scoped>
.panel{background:#0d1b2a;border-radius:8px;padding:12px;border:1px solid #1e3a5f;flex:1}
.panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.panel h4{color:#f87171;font-size:13px}
.user-select{width:180px}
.load-error{color:#fca5a5;font-size:11px;margin-bottom:6px}
.empty{color:#64748b;font-size:12px}
.anomaly-row{display:flex;gap:8px;padding:4px 0;font-size:11px;color:#fca5a5;flex-wrap:wrap;align-items:center}
.anomaly-row.clickable{cursor:pointer}
.anomaly-row.clickable:hover{background:#112233;border-radius:4px}
.a-time{color:#64748b;min-width:70px}
.a-tag{background:#7f1d1d33;padding:1px 6px;border-radius:3px;border:1px solid #7f1d1d55}
.a-meta{margin-left:auto;display:flex;gap:6px;align-items:center}
.a-owner{color:#93c5fd;font-size:11px}
.a-owner.unassigned{color:#fbbf24}
.a-note{flex-basis:100%;color:#cbd5e1;font-size:11px;padding-left:78px}
.dlg-line{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;font-size:12px}
.deny-alert{margin-bottom:12px}
.full{width:100%}
.field-hint{font-size:11px;color:#94a3b8;margin-top:2px}
.field-hint.warn{color:#fbbf24}
.updated-hint{font-size:11px;color:#64748b;padding-left:84px}
</style>
