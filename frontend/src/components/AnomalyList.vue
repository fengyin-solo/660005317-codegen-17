<template>
  <div class="panel">
    <h4>⚠️ 近期告警</h4>
    <div v-if="!auth.isLoggedIn" class="empty hint-login">未登录：仅可查看实时告警，登录后可查看责任归属并进行处置</div>
    <div v-if="!anomalies.length" class="empty">暂无告警</div>
    <div v-for="a in anomalies" :key="keyOf(a)" class="anomaly-row">
      <span class="a-time">{{ ts(a.timestamp) }}</span>
      <span v-for="t in a.triggers" :key="t.rule" class="a-tag">{{ t.rule }}: {{ t.value.toFixed(1) }}</span>

      <!-- 责任归属与处置状态（不改动原有告警标签样式） -->
      <div class="a-handling">
        <span class="a-status" :class="statusClass(a.status)">{{ statusText(a.status) }}</span>
        <span class="a-assignee">
          👤 {{ a.assignee ? displayName(a.assignee) + '（' + a.assignee + '）' : '待认领（未指定处置人）' }}
        </span>
        <span class="a-note" :class="{ muted: !a.note }">📝 {{ a.note || '暂无处置说明' }}</span>
        <span v-if="a.updated_by" class="a-updated">最后更新: {{ displayName(a.updated_by) }}</span>

        <template v-if="auth.isLoggedIn">
          <el-button
            v-if="canEdit(a)"
            size="small" type="primary" plain class="a-btn"
            @click="openDialog(a)"
          >{{ a.assignee ? '修改处置' : '认领/处置' }}</el-button>
          <span v-else class="a-lock" :title="lockReason(a)">🔒 {{ lockReason(a) }}</span>
        </template>
      </div>
    </div>

    <!-- 处置编辑对话框：仅本人或管理员可走到保存；越权/不合格由后端再次拦截 -->
    <el-dialog v-model="dialogVisible" title="告警处置" width="460px" @closed="resetDialog">
      <div v-if="editing" class="dlg">
        <div class="dlg-target">
          <span class="a-time">{{ ts(editing.timestamp) }}</span>
          <span v-for="t in editing.triggers" :key="t.rule" class="a-tag">{{ t.rule }}: {{ t.value.toFixed(1) }}</span>
        </div>

        <el-alert
          v-if="errorReason"
          :title="errorReason"
          type="error" :closable="false" show-icon class="dlg-alert"
        >
          <ul v-if="invalidItems.length" class="invalid-list">
            <li v-for="(item, i) in invalidItems" :key="i">{{ item }}</li>
          </ul>
        </el-alert>

        <div class="field">
          <label>处置人 <span class="req">*</span></label>
          <el-select v-model="form.assignee" placeholder="请选择处置人" :disabled="assigneeLocked" style="width:100%">
            <el-option
              v-for="u in assignableUsers" :key="u.username"
              :label="u.display_name + (assignEligible(u) ? '' : '（不可指派）')"
              :value="u.username"
            />
          </el-select>
          <div v-if="assigneeLocked" class="hint">
            {{ auth.isAdmin ? '管理员可改派给任意处置人' : '非管理员仅可自行认领；改派处置人需联系管理员' }}
          </div>
        </div>

        <div class="field">
          <label>处置状态 <span class="req">*</span></label>
          <el-select v-model="form.status" style="width:100%">
            <el-option label="待处理 PENDING" value="PENDING" />
            <el-option label="处理中 IN_PROGRESS" value="IN_PROGRESS" />
            <el-option label="已解决 RESOLVED" value="RESOLVED" />
          </el-select>
        </div>

        <div class="field">
          <label>处置说明</label>
          <el-input v-model="form.note" type="textarea" :rows="3" placeholder="请填写处置过程与结论" />
        </div>
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useFactoryStore } from '../store/factory'
import { useAuthStore } from '../store/auth'
import { ApiError } from '../api'
import type { Anomaly, CurrentUser } from '../types'

const store = useFactoryStore()
const auth = useAuthStore()
const anomalies = computed(() => store.anomalies)

function ts(t: number) { return new Date(t * 1000).toLocaleTimeString() }
function keyOf(a: Anomaly) { return a.id ?? `${a.timestamp}-${a.triggers[0]?.rule}` }

const STATUS_TEXT: Record<string, string> = {
  PENDING: '待处理', IN_PROGRESS: '处理中', RESOLVED: '已解决',
}
function statusText(s?: string) { return STATUS_TEXT[s || 'PENDING'] || s || '待处理' }
function statusClass(s?: string) { return `st-${(s || 'PENDING').toLowerCase()}` }

const userMap = computed<Record<string, CurrentUser>>(() =>
  Object.fromEntries(auth.users.map((u) => [u.username, u])))
function displayName(username?: string | null) {
  return username ? (userMap.value[username]?.display_name || username) : ''
}
// 处置人下拉展示全部账号（只读/未配置账号会标注“不可指派”，由后端拒绝并说明）
const assignableUsers = computed(() => auth.users)
function assignEligible(u: CurrentUser) {
  return u.configured && u.permissions.includes('anomaly:handle')
}

function canEdit(a: Anomaly) {
  return auth.canEditAnomaly(a.assignee) || auth.canClaim(a.assignee)
}
function lockReason(a: Anomaly): string {
  const me = auth.user
  if (!me) return '请先登录'
  if (!me.configured) return `当前角色「${me.role}」权限配置缺失，仅可查看，请联系管理员`
  if (!auth.canHandle) return '当前账号为只读权限，仅处置人本人或管理员可修改'
  if (!a.assignee) return '仅可自行认领（点击“认领/处置”）'
  return `仅处置人 ${a.assignee} 本人或管理员可修改`
}

// ---- 编辑对话框 ----
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<Anomaly | null>(null)
const errorReason = ref('')
const invalidItems = ref<string[]>([])
const form = reactive({ assignee: '', status: 'PENDING', note: '' })

const assigneeLocked = computed(() => {
  if (auth.isAdmin) return false
  return true // 非管理员：未认领时只能选自己、已认领时不能改派
})

function openDialog(a: Anomaly) {
  editing.value = a
  errorReason.value = ''
  invalidItems.value = []
  const me = auth.user!.username
  const isAdmin = auth.isAdmin
  form.assignee = a.assignee || (isAdmin ? '' : me)
  form.status = a.status || 'PENDING'
  form.note = a.note || ''
  dialogVisible.value = true
}

function resetDialog() {
  editing.value = null
  errorReason.value = ''
  invalidItems.value = []
  saving.value = false
}

async function save() {
  if (!editing.value || typeof editing.value.id !== 'number') return
  errorReason.value = ''
  invalidItems.value = []
  saving.value = true
  try {
    await store.saveHandling(editing.value.id, {
      assignee: form.assignee, status: form.status, note: form.note,
    })
    ElMessage.success('处置信息已保存')
    dialogVisible.value = false
  } catch (e) {
    if (e instanceof ApiError) {
      errorReason.value = e.status === 403
        ? e.message
        : `保存被拒绝：${e.message}`
      invalidItems.value = e.invalidItems
    } else {
      errorReason.value = '网络异常，保存失败，请重试'
    }
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.panel{background:#0d1b2a;border-radius:8px;padding:12px;border:1px solid #1e3a5f;flex:1}
.panel h4{color:#f87171;margin-bottom:8px;font-size:13px}
.empty{color:#64748b;font-size:12px}
.anomaly-row{display:flex;gap:8px;padding:4px 0;font-size:11px;color:#fca5a5;flex-wrap:wrap;border-bottom:1px solid #1e3a5f33}
.a-time{color:#64748b;min-width:70px}
.a-tag{background:#7f1d1d33;padding:1px 6px;border-radius:3px;border:1px solid #7f1d1d55;display:inline-block}
.a-handling{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:2px;width:100%;color:#94a3b8}
.a-status{padding:1px 6px;border-radius:3px;font-weight:600}
.st-pending{background:#7f1d1d55;color:#fca5a5;border:1px solid #7f1d1d}
.st-in_progress{background:#78350f55;color:#fbbf24;border:1px solid #92400e}
.st-resolved{background:#14532d55;color:#4ade80;border:1px solid #166534}
.a-assignee{color:#cbd5e1}
.a-note{color:#a5b4fc}
.a-note.muted{color:#64748b}
.a-updated{color:#64748b}
.a-lock{color:#f59e0b;font-size:10px;margin-left:auto}
.a-btn{margin-left:auto}
.dlg .dlg-target{margin-bottom:12px}
.dlg .field{margin-bottom:12px}
.dlg .field label{display:block;font-size:12px;color:#475569;margin-bottom:4px}
.dlg .req{color:#ef4444}
.dlg .hint{font-size:11px;color:#94a3b8;margin-top:4px}
.dlg-alert{margin-bottom:12px}
.invalid-list{margin:6px 0 0 16px;padding:0}
.invalid-list li{margin:2px 0}
</style>
