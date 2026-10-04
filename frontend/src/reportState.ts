import type { JobStatus, ReportReview } from './types'

/** 审核决定与保存状态分别读取；有正式报告编号才显示保存成功。 */
export function reportState(review: ReportReview, jobStatus: JobStatus) {
  if (review.report_id) return '正式报告已保存'
  if (jobStatus === 'cancelled') return '已取消'
  if (review.status === 'rejected') return '候选已拒绝'
  if (jobStatus === 'failed') return review.status === 'approved' ? '报告保存失败' : '审核流程中断'
  if (review.status === 'approved') return '报告保存中'
  return '待人工审核'
}
