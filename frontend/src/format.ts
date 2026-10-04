import { ApiError } from './api'

export function number(value: number | null, digits = 3) {
  return value === null ? '未定义' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value)
}
export function timestamp(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium', hour12: false }).format(new Date(value))
}
export function engineLabel(id: number) { return `发动机 ${String(id).padStart(3, '0')}` }
export const roleLabel: Record<string, string> = { admin: '管理员', analyst: '分析员', reviewer: '审核员', guest: '访客' }
export const riskLabel = { normal: '常规关注', warning: '加强关注', critical: '高风险关注' }
export function errorText(error: unknown) {
  if (error instanceof ApiError) return `${error.message}（${error.code}${error.requestId ? ` · 请求 ${error.requestId}` : ''}）`
  return error instanceof Error ? error.message : '处理失败，请重新检查。'
}
export function cancelled(error: unknown) { return error instanceof DOMException && error.name === 'AbortError' }
