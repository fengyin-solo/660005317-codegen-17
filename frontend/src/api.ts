// 统一的后端请求封装：自动带上登录令牌，并把后端的拒绝原因原样抛出
import { useAuthStore } from '@/store/auth'

export class ApiError extends Error {
  status: number
  invalidItems: string[]
  constructor(status: number, reason: string, invalidItems: string[] = []) {
    super(reason)
    this.status = status
    this.invalidItems = invalidItems
  }
}

export async function api<T = any>(
  path: string,
  options: { method?: string; body?: unknown } = {}
): Promise<T> {
  const auth = useAuthStore()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`

  const res = await fetch(path, {
    method: options.method || 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })

  let data: any = null
  try { data = await res.json() } catch { /* 非JSON响应 */ }

  if (!res.ok) {
    const detail = data?.detail
    if (detail && typeof detail === 'object' && detail.reason) {
      throw new ApiError(res.status, detail.reason, detail.invalid_items || [])
    }
    throw new ApiError(res.status, `请求失败（HTTP ${res.status}）`)
  }
  return data as T
}
