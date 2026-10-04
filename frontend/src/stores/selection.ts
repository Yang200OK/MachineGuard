import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { PredictionAttempt } from '../types'

interface Selection { engineKey: string; endCycle: number; page: number; attempts: PredictionAttempt[] }
const KEY = 'machineguard.selection'

export const useSelectionStore = defineStore('selection', () => {
  const selected = ref<Selection | null>(null)
  function restore() {
    const saved = sessionStorage.getItem(KEY)
    if (!saved) { selected.value = null; return }
    const value = JSON.parse(saved) as Selection
    if (typeof value.engineKey !== 'string' || !Number.isInteger(value.endCycle) || value.endCycle < 1 || !Number.isInteger(value.page) || value.page < 1 ||
        !Array.isArray(value.attempts) || value.attempts.some(attempt => typeof attempt.engine_key !== 'string' || !Number.isInteger(attempt.end_cycle) ||
        attempt.end_cycle < 1 || typeof attempt.model_version !== 'string' || typeof attempt.idempotency_key !== 'string' || !/^[A-Za-z0-9_.-]{1,64}$/.test(attempt.idempotency_key))) {
      throw new Error('设备选择缓存无效，请清除当前选择后重新选择。')
    }
    selected.value = value
  }
  function save(engineKey: string, endCycle: number, page: number) {
    const prior = selected.value
    // 保存本会话发起过的窗口请求键，切换设备后返回原窗口也不重复入库。
    const attempts = prior?.attempts ?? []
    selected.value = { engineKey, endCycle, page, attempts }
    sessionStorage.setItem(KEY, JSON.stringify(selected.value))
  }
  function clear() { selected.value = null; sessionStorage.removeItem(KEY) }
  function attempt(modelVersion: string): PredictionAttempt {
    if (!selected.value) throw new Error('请先选择设备与周期。')
    const prior = selected.value.attempts.find(value => value.model_version === modelVersion &&
      value.engine_key === selected.value?.engineKey && value.end_cycle === selected.value.endCycle)
    if (prior) return prior
    const value = { engine_key: selected.value.engineKey, end_cycle: selected.value.endCycle, model_version: modelVersion, idempotency_key: crypto.randomUUID() }
    selected.value.attempts.push(value)
    sessionStorage.setItem(KEY, JSON.stringify(selected.value))
    return value
  }
  return { selected, restore, save, clear, attempt }
})
