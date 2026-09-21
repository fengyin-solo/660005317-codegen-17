export interface Device {
  id: number; type: string; status: string; position: number[]
  temperature: number; vibration: number; pressure: number
  production_count: number; fault_count: number
  uptime: number; quality_rate: number
}

export interface Anomaly {
  id?: number
  timestamp: number; triggers: { device_id: number; rule: string; value: number; threshold: string | number }[]
  device_type: string
  // 责任归属与处置
  assignee?: string | null
  note?: string
  status?: 'PENDING' | 'IN_PROGRESS' | 'RESOLVED' | string
  updated_by?: string | null
  updated_at?: number | null
}

export interface CurrentUser {
  username: string
  display_name: string
  role: string
  permissions: string[]
  configured: boolean
}

export interface OEEItem {
  id: number; type: string; oee: number
  availability: number; performance: number; quality: number
}

export interface FactoryData {
  devices: Device[]
  production: number
  anomalies: Anomaly[]
  oee: OEEItem[]
}

export const DEVICE_COLORS: Record<string, string> = {
  CNC: '#e74c3c', RobotArm: '#3498db', Conveyor: '#f39c12',
  AGV: '#2ecc71', InjectionMolding: '#9b59b6', QCStation: '#1abc9c'
}

export const STATUS_COLORS: Record<string, string> = {
  RUNNING: '#2ecc71', IDLE: '#f1c40f', FAULT: '#e74c3c', OFFLINE: '#95a5a6'
}