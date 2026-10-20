<script setup>
import { ref } from 'vue'

defineProps({
  loading: Boolean,
  error: { type: String, default: '' },
})

const emit = defineEmits(['submit'])
const mode = ref('login')
const username = ref('')
const password = ref('')

const submit = () => {
  emit('submit', {
    mode: mode.value === 'register' ? 'register' : 'login',
    username: username.value,
    password: password.value,
  })
}
</script>

<template>
  <section class="auth-shell">
    <form class="auth-card" @submit.prevent="submit">
      <div class="auth-mark" aria-hidden="true">✿</div>
      <p class="auth-eyebrow">FLORA · 花伴</p>
      <h1>{{ mode === 'register' ? '创建你的账号' : '欢迎回来' }}</h1>
      <p class="auth-description">登录后，聊天记录会安全地保存在你的账号下。</p>

      <label class="auth-field">
        <span>用户名</span>
        <input
          v-model="username"
          autocomplete="username"
          minlength="3"
          maxlength="32"
          pattern="[A-Za-z0-9_.-]{3,32}"
          required
          placeholder="3–32 位字母、数字或 . _ -"
        />
      </label>
      <label class="auth-field">
        <span>密码</span>
        <input
          v-model="password"
          :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
          type="password"
          minlength="8"
          maxlength="128"
          required
          placeholder="至少 8 个字符"
        />
      </label>
      <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
      <button class="auth-submit" type="submit" :disabled="loading">
        {{ loading ? '请稍候…' : mode === 'register' ? '注册并登录' : '登录' }}
      </button>
      <button
        class="auth-switch"
        type="button"
        :disabled="loading"
        @click="mode = mode === 'register' ? 'login' : 'register'"
      >
        {{ mode === 'register' ? '已有账号？登录' : '还没有账号？创建账号' }}
      </button>
    </form>
  </section>
</template>
