<script setup>
defineProps({ loading: Boolean })
defineEmits(['select-suggestion'])

const suggestions = [
  { icon: '☀', title: '阳台适合养什么花？', text: '我的阳台日照充足，适合养什么花？' },
  { icon: '✿', title: '月季怎么养护？', text: '月季日常应该怎么养护？' },
  { icon: '⌁', title: '查一下上海天气', text: '帮我查一下上海今天的天气' },
]
</script>

<template>
  <div class="welcome-state">
    <div class="welcome-orb" aria-hidden="true">
      <span class="orb-leaf">✿</span>
      <span class="orb-sparkle">✦</span>
    </div>
    <div class="eyebrow">YOUR PERSONAL GARDENING GUIDE</div>
    <h1>今天想和花聊些什么？</h1>
    <p class="welcome-copy">
      从一片叶子的变化，到阳台花园的规划，<br class="desktop-break" />我都可以陪你一起找到答案。
    </p>

    <div class="suggestion-grid">
      <button
        v-for="suggestion in suggestions"
        :key="suggestion.title"
        class="suggestion-card"
        type="button"
        :disabled="loading"
        @click="$emit('select-suggestion', suggestion.text)"
      >
        <span class="suggestion-icon">{{ suggestion.icon }}</span>
        <span>{{ suggestion.title }}</span>
        <span class="suggestion-arrow" aria-hidden="true">↗</span>
      </button>
    </div>
    <div class="welcome-note"><span>✦</span> 试着描述植物的名字、状态或你所在的城市</div>
  </div>
</template>
