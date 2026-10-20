<script setup>
const escapeHtml = (value) => value
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#39;')

const renderInlineMarkdown = (value) => {
  let text = escapeHtml(value)
  const codeSpans = []
  text = text.replace(/`([^`]+)`/g, (_, code) => {
    const marker = `\u0000${codeSpans.length}\u0000`
    codeSpans.push(`<code>${code}</code>`)
    return marker
  })
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  text = text.replace(/__(.+?)__/g, '<strong>$1</strong>')
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>')
  text = text.replace(/~~(.+?)~~/g, '<del>$1</del>')
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => codeSpans[Number(index)])
}

const renderMarkdown = (value = '') => {
  const lines = value.split(/\r?\n/)
  const blocks = []
  let paragraph = []
  let listType = ''
  let codeLines = null

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(`<p>${paragraph.map(renderInlineMarkdown).join('<br>')}</p>`)
    paragraph = []
  }
  const closeList = () => {
    if (!listType) return
    blocks.push(`</${listType}>`)
    listType = ''
  }

  for (const line of lines) {
    if (codeLines) {
      if (/^\s*```/.test(line)) {
        blocks.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = null
      } else {
        codeLines.push(line)
      }
      continue
    }
    if (/^\s*```/.test(line)) {
      flushParagraph()
      closeList()
      codeLines = []
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      closeList()
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    const listItem = line.match(/^\s*([-*+] |\d+\. )(.*)$/)
    const quote = line.match(/^\s*>\s?(.*)$/)
    if (heading) {
      flushParagraph()
      closeList()
      const level = heading[1].length
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`)
    } else if (listItem) {
      flushParagraph()
      const nextType = /^\s*\d/.test(line) ? 'ol' : 'ul'
      if (listType !== nextType) {
        closeList()
        listType = nextType
        blocks.push(`<${listType}>`)
      }
      blocks.push(`<li>${renderInlineMarkdown(listItem[2])}</li>`)
    } else if (quote) {
      flushParagraph()
      closeList()
      blocks.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`)
    } else {
      closeList()
      paragraph.push(line)
    }
  }

  flushParagraph()
  closeList()
  if (codeLines) blocks.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  return blocks.join('')
}

defineProps({
  messages: { type: Array, required: true },
  loading: Boolean,
})
</script>

<template>
  <div class="message-list">
    <div
      v-for="(message, index) in messages"
      :key="index"
      v-show="message.role === 'user' || message.content || message.diagnosis || message.identification"
      class="message-row"
      :class="message.role === 'user' ? 'from-user' : 'from-assistant'"
    >
      <div v-if="message.role !== 'user'" class="assistant-avatar" aria-hidden="true">✿</div>
      <div class="message-content">
        <div v-if="message.role !== 'user'" class="message-label">
          花伴<span v-if="message.isPartial" class="partial-message-label">（连接中断，部分回答）</span>
        </div>
        <div v-if="message.diagnosis" class="identification-card diagnosis-card">
          <div class="identification-heading">
            <strong>病虫害初步诊断</strong>
            <span v-if="message.diagnosis.uncertain" class="uncertainty-badge">仍需确认</span>
          </div>
          <p class="identification-summary">{{ message.diagnosis.summary }}</p>
          <div v-if="message.diagnosis.issues?.length" class="candidate-list">
            <article
              v-for="(issue, issueIndex) in message.diagnosis.issues"
              :key="`${issue.name}-${issueIndex}`"
              class="candidate-item"
            >
              <div class="candidate-heading">
                <strong>{{ issue.name }}</strong>
                <span>{{ issue.confidence }}把握</span>
              </div>
              <p>{{ issue.evidence }}</p>
            </article>
          </div>
          <div v-if="message.diagnosis.recommendations?.length" class="diagnosis-recommendations">
            <strong>建议先这样处理</strong>
            <ul>
              <li v-for="(recommendation, recommendationIndex) in message.diagnosis.recommendations" :key="recommendationIndex">
                {{ recommendation }}
              </li>
            </ul>
          </div>
          <p class="identification-scope-note">这是基于图片的初步判断；若症状持续或加重，建议补充叶片正反面、茎部和整株照片。</p>
          <div v-if="message.diagnosis.clarifying_question" class="clarifying-question">
            {{ message.diagnosis.clarifying_question }}
          </div>
        </div>
        <div v-else-if="message.identification" class="identification-card">
          <div class="identification-heading">
            <strong>识别结果</strong>
            <span v-if="message.identification.uncertain" class="uncertainty-badge">仍需确认</span>
          </div>
          <p class="identification-summary">{{ message.identification.summary }}</p>
          <p class="identification-scope-note">
            图片通常只能判断到常见类别或物种；具体栽培品种还需要更多特征确认。
          </p>

          <div v-if="message.identification.candidates.length" class="candidate-list">
            <article
              v-for="(candidate, candidateIndex) in message.identification.candidates"
              :key="`${candidate.name}-${candidateIndex}`"
              class="candidate-item"
            >
              <div class="candidate-heading">
                <strong>{{ candidate.name }}</strong>
                <span>{{ candidate.confidence }}把握</span>
              </div>
              <p>{{ candidate.evidence }}</p>
            </article>
          </div>

          <div v-for="item in message.identification.knowledge" :key="item.flower" class="knowledge-result">
            <div class="knowledge-heading">{{ item.flower }} · 养护资料</div>
            <p v-for="(passage, passageIndex) in item.passages" :key="passageIndex">{{ passage }}</p>
          </div>
          <p
            v-if="message.identification.uncertain && message.identification.candidates.length && !message.identification.knowledge.length"
            class="knowledge-caution"
          >识别仍有不确定性，暂不关联特定花卉的养护资料，以免误导。</p>
          <div v-if="message.identification.clarifying_question" class="clarifying-question">
            {{ message.identification.clarifying_question }}
          </div>
        </div>
        <div v-else class="message-bubble" :class="{ 'error-bubble': message.isError }">
          <div class="markdown-body" v-html="renderMarkdown(message.content || '')"></div>
        </div>
      </div>
      <div v-if="message.role === 'user'" class="user-avatar" aria-hidden="true">我</div>
    </div>

    <div v-if="loading" class="message-row from-assistant">
      <div class="assistant-avatar" aria-hidden="true">✿</div>
      <div class="message-content">
        <div class="message-label">花伴</div>
        <div class="message-bubble typing-bubble" aria-label="正在思考">
          <span></span><span></span><span></span>
        </div>
      </div>
    </div>
  </div>
</template>
