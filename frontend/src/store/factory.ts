import { defineStore } from 'pinia'
import { ref, computed, onUnmounted } from 'vue'
import type { FactoryData, Anomaly } from '@/types'
import { api } from '@/api'

export const useFactoryStore = defineStore('factory', () => {
  const data = ref<FactoryData | null>(null)
  const ws = ref<WebSocket | null>(null)
  const connected = ref(false)
  // 处置信息（assignee/note/status…）以告警 id 为键，来自 REST 全量列表与保存响应。
  // 实时推送只负责告警本身的条数/时间/标签，处置信息由这里叠加，避免互相覆盖。
  const handlingMap = ref<Record<number, Anomaly>>({})

  function absorbHandling(list: Anomaly[]) {
    for (const a of list) {
      if (typeof a.id === 'number') handlingMap.value[a.id] = { ...handlingMap.value[a.id], ...a }
    }
  }

  async function fetchAnomalies() {
    const res = await api<{ anomalies: Anomaly[] }>('/api/anomalies')
    absorbHandling(res.anomalies)
  }

  async function saveHandling(id: number, payload: { assignee: string; status: string; note: string }) {
    const res = await api<{ anomaly: Anomaly }>(`/api/anomalies/${id}/handling`, {
      method: 'PUT', body: payload,
    })
    absorbHandling([res.anomaly])
    // 保存后再拉一次列表，保证返回列表时处置说明与服务端完全一致
    await fetchAnomalies()
    return res.anomaly
  }

  // 列表显示仍以实时数据（最近5条）为准，只叠加处置字段，条数/顺序/颜色均不变
  const anomalies = computed<Anomaly[]>(() => {
    const list = data.value?.anomalies || []
    return list.map((a) => {
      const stored = typeof a.id === 'number' ? handlingMap.value[a.id] : undefined
      // 处置字段以服务端保存值为准（stored），实时推送仅补充告警本身的条数/时间/标签
      return stored ? { ...a, ...pickHandling(stored) } : a
    })
  })

  function pickHandling(a: Anomaly) {
    return {
      assignee: a.assignee ?? null,
      note: a.note ?? '',
      status: a.status ?? 'PENDING',
      updated_by: a.updated_by ?? null,
      updated_at: a.updated_at ?? null,
    }
  }

  function connect() {
    if (ws.value) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const s = new WebSocket(`${protocol}//${location.hostname}:8000/ws`)
    s.onopen = () => { connected.value = true; console.log('WS connected') }
    s.onmessage = (e) => {
      try { data.value = JSON.parse(e.data) } catch {}
    }
    s.onclose = () => { connected.value = false; ws.value = null }
    ws.value = s
  }

  function disconnect() {
    ws.value?.close()
    ws.value = null
    connected.value = false
  }

  return { data, connected, anomalies, handlingMap, connect, disconnect, fetchAnomalies, saveHandling, absorbHandling }
})
