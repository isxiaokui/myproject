<script setup>
defineProps({
  conversations: { type: Array, default: () => [] },
  currentConversationId: { type: String, default: null },
  loading: Boolean,
  historyLoading: Boolean,
  error: { type: String, default: '' },
})

const emit = defineEmits(['new', 'select', 'delete'])

const formatDate = (value) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(date)
}

const requestDelete = (item) => {
  if (window.confirm(`确定删除“${item.title || '新对话'}”吗？此操作无法撤销。`)) {
    emit('delete', item.id)
  }
}

</script>

<template>
  <aside class="history-sidebar" aria-label="历史会话">
    <div class="history-heading">
      <div>
        <p class="history-eyebrow">YOUR GARDEN</p>
        <h2>对话记录</h2>
      </div>
      <span class="history-count">{{ conversations.length }}</span>
    </div>

    <button class="new-conversation-button" type="button" :disabled="loading" @click="$emit('new')">
      <span aria-hidden="true">＋</span> 开启新对话
    </button>

    <p v-if="error" class="history-error" role="alert">{{ error }}</p>
    <div v-if="historyLoading" class="history-empty">正在载入会话…</div>
    <div v-else-if="!conversations.length" class="history-empty">
      <span aria-hidden="true">✿</span>
      <p>还没有对话记录</p>
      <small>聊过的内容会保存在这里</small>
    </div>
    <nav v-else class="history-list" aria-label="会话列表">
      <div
        v-for="item in conversations"
        :key="item.id"
        class="history-item"
        :class="{ 'is-active': currentConversationId === item.id }"
      >
        <button
          class="history-select"
          type="button"
          :disabled="loading"
          :aria-current="currentConversationId === item.id ? 'page' : undefined"
          @click="$emit('select', item.id)"
        >
          <span class="history-flower" aria-hidden="true">✿</span>
          <span class="history-item-copy">
            <strong>{{ item.title || '新对话' }}</strong>
            <small>{{ formatDate(item.updated_at) }}</small>
          </span>
        </button>
        <button
          class="history-delete"
          type="button"
          :disabled="loading"
          :aria-label="`删除会话：${item.title || '新对话'}`"
          title="删除会话"
          @click="requestDelete(item)"
        >×</button>
      </div>
    </nav>

    <div class="history-footer"><span></span> 会话已保存到 PostgreSQL</div>
  </aside>
</template>
