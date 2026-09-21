import axios from 'axios'
import { useAuthStore } from '../store/auth'

// 统一请求实例: 自动带上当前登录账号 X-User-Id
export const http = axios.create({ timeout: 8000 })

http.interceptors.request.use((cfg) => {
  const auth = useAuthStore()
  if (auth.userId) cfg.headers['X-User-Id'] = auth.userId
  return cfg
})

// 统一提取后端 {detail:{error,reasons}} 错误结构
export interface ApiError {
  status: number
  error: string
  reasons: string[]
}

export function parseError(e: unknown): ApiError {
  if (axios.isAxiosError(e) && e.response) {
    const status = e.response.status
    const detail = e.response.data?.detail
    if (detail && typeof detail === 'object') {
      return {
        status,
        error: detail.error || '请求失败',
        reasons: Array.isArray(detail.reasons) ? detail.reasons : [String(detail)],
      }
    }
    return { status, error: typeof detail === 'string' ? detail : '请求失败', reasons: [e.message] }
  }
  return { status: 0, error: '网络异常', reasons: [(e as Error)?.message || '无法连接服务器'] }
}
