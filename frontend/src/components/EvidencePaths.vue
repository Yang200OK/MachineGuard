<script setup lang="ts">
import type { GraphPathResult } from '../graphTypes'
defineProps<{ result: GraphPathResult; chooseDocument?: boolean }>()
defineEmits<{ source: [path: number, edge: number]; document: [id: string] }>()
</script>

<template>
  <div class="evidence-paths" aria-label="证据路径结果">
    <p v-if="result.reason" class="scope-note">{{ result.reason }}</p>
    <div v-if="result.candidates.length" class="path-candidates"><p>同名实体所在资料：</p>
      <div v-for="candidate in result.candidates" :key="candidate.entity_id"><strong>{{ candidate.name }} · {{ candidate.document_title }}</strong><p class="method-caption">{{ candidate.kind }} · 文档 {{ candidate.document_id }}</p>
        <el-button v-if="chooseDocument" @click="$emit('document', candidate.document_id)">使用此文档查询</el-button></div>
    </div>
    <article v-for="(path, pathIndex) in result.paths" :key="pathIndex" class="path-card">
      <h3>路径 {{ pathIndex + 1 }} · {{ path.hops }} 跳</h3>
      <p class="path-chain">{{ path.edges[0]?.subject.name }}<template v-for="edge in path.edges" :key="edge.relation_id"> — {{ edge.predicate }} → {{ edge.object.name }}</template></p>
      <div v-for="(edge, edgeIndex) in path.edges" :key="edge.relation_id" class="path-edge">
        <p class="method-caption">{{ edge.subject.document_title }} · 版本 {{ edge.citation.document_version }} · 页 {{ edge.citation.page_spans.map(span => span.page).join('、') }} · 审核修订 {{ edge.revision }}</p>
        <el-button link type="primary" @click="$emit('source', pathIndex, edgeIndex)">第 {{ edgeIndex + 1 }} 跳 · {{ edge.predicate }} · 查看原文</el-button>
      </div>
    </article>
    <p v-if="result.truncated" class="scope-note">已显示前 {{ result.paths.length }} 条路径，还有其他路径；可指定终点缩小范围。</p>
    <p class="method-caption">{{ result.limitation }}</p>
  </div>
</template>

<style scoped>
.evidence-paths{line-height:1.8;overflow-wrap:anywhere}.path-card{margin:18px 0;padding:18px;border:1px solid #dce5df;border-radius:10px}.path-chain{margin:12px 0;color:#235b46;font-size:15px}.path-edge{padding:14px 0;border-top:1px solid #e4ebe6}.path-edge blockquote{margin:12px 0;padding:12px;border-left:3px solid #9bc3a9;background:#f5f8f5;white-space:pre-wrap;font-size:13px}.path-candidates>div{margin:14px 0;padding:12px;background:#f5f8f5}.path-candidates p{margin:8px 0}
</style>
