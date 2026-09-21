import { defineStore } from 'pinia'
import { ref, onUnmounted } from 'vue'
import type { FactoryData, Anomaly } from '@/types'

export const useFactoryStore = defineStore('factory', () => {
  const data = ref<FactoryData | null>(null)
  const ws = ref<WebSocket | null>(null)
  const connected = ref(false)

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

  // 保存处置结果后本地同步, 保证返回列表时处置说明/状态立即一致
  // (后续 WS 推送以服务端为准, 内容相同不会产生跳变)
  function patchAnomaly(updated: Anomaly) {
    if (!data.value) return
    const list = data.value.anomalies
    const idx = list.findIndex((a) => a.id === updated.id)
    if (idx >= 0) {
      list[idx] = { ...list[idx], ...updated }
    }
  }

  return { data, connected, connect, disconnect, patchAnomaly }
})
