<script setup>
defineProps({
  hasMessages: Boolean,
  loading: Boolean,
  username: { type: String, default: '' },
})

defineEmits(['clear', 'logout'])
</script>

<template>
  <header class="topbar">
    <div class="brand-lockup">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" fill="none">
          <path d="M25.8 6.2C16 6.3 8.6 8.1 6.2 14.4c-1.4 3.7.8 7.5 4.6 7.5 7.4 0 12.6-6.4 15-15.7Z" fill="currentColor" opacity=".92" />
          <path d="M6.1 26c3.8-6.2 8.3-10.2 14.2-13.6" stroke="white" stroke-width="1.8" stroke-linecap="round" />
          <path d="M7.2 10.4c-2.2 1.5-3.3 3.8-3.1 6.1 2.6.3 4.8-.4 6.4-2.2" fill="currentColor" opacity=".58" />
        </svg>
      </div>
      <div>
        <div class="brand-name">花伴 <span>Flora</span></div>
        <div class="brand-caption">懂花，也懂你的日常</div>
      </div>
    </div>
    <div class="topbar-right">
      <span class="status-pill" aria-label="智能助手在线">
        <i aria-hidden="true"></i>
        <span class="status-label-full" aria-hidden="true">智能助手在线</span>
        <span class="status-label-short" aria-hidden="true">在线</span>
      </span>
      <span class="account-name">{{ username }}</span>
      <button class="logout-button" type="button" :disabled="loading" @click="$emit('logout')">退出</button>
      <button
        v-if="hasMessages"
        class="clear-button"
        type="button"
        :disabled="loading"
        @click="$emit('clear')"
      >
        <span aria-hidden="true">↺</span> 新对话
      </button>
    </div>
  </header>
</template>
