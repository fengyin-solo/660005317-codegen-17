export interface Device {
  id: number; type: string; status: string; position: number[]
  temperature: number; vibration: number; pressure: number
  production_count: number; fault_count: number
  uptime: number; quality_rate: number
}

export type AlertStatus = 'PENDING' | 'PROCESSING' | 'RESOLVED'

export interface Anomaly {
  id?: number
  timestamp: number; triggers: { device_id: number; rule: string; value: number; threshold: string }[]
  device_type: string
  // 责任归属与处置信息
  assignee?: string | null
  assignee_name?: string | null
  status?: AlertStatus
  note?: string
  updated_by?: string | null
  updated_at?: number | null
}

export interface User {
  id: string; name: string; role: string
}

export interface RolePermission {
  label: string
  can_handle: 'any' | 'assigned' | 'none'
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