<script setup>
 import { ref } from 'vue'
 const input = ref('')
 const messages = ref([])
 const loading = ref(false)
const sendMessage = async () => {
    if (loading.value == true) return
    if (input.value.trim() === '') return
    loading.value = true
    try {
        messages.value.push({ role: 'user', content: input.value })

        const res = await fetch("http://127.0.0.1:3001/chat", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ msg: messages.value })
        })
        input.value = ''
        const result = await res.json()
        console.log(result)
        messages.value.push({ role: 'assistant', content: result.message })
    }finally {
    loading.value = false
    }
 }
</script>
<template>
  <div class="whole-layout">
    <el-container class="common-layout">
      <el-header class="app-header">花店智能客服</el-header>
      <el-main class="app-main">
        <div v-for="(message, index) in messages" :key="index" class="msg" :class="{ 'user' : message.role === 'user' , 'assistant' : message.role === 'assistant' }">
          {{ message.content }}
        </div>
      </el-main>
      <el-footer>
        <div class="input-row">
          <el-input v-model="input" class="chat-input" placeholder="请输入内容" @keyup.enter="sendMessage"/>
          <el-button @click="sendMessage" :loading="loading ">发送</el-button>
        </div>
      </el-footer>
    </el-container>
  </div>
</template>

<style scoped>
    .whole-layout {
        height: 100vh;
    }
    .common-layout {
        height: 100%;
    }
    .app-header {
        display:flex;
        justify-content:center; /* 水平 */
        align-items:center;    /* 垂直 */
        border-bottom:1px solid #12f337;
    }
    .input-row {
        display: flex;   /* 横向排列 */
        gap: 8px;        /*子元素之间的间距 */
        padding: 8px;     /* 内边距 */

    }
    .chat-input {
        flex: 1;         /* 占据剩余空间 */
    }
    .msg {
        /* border: 1px solid #f34545; */
        max-width: fit-content;
        margin: 8px;
        border-radius: 24px;
        padding: 16rpx 20rpx;
    }
    .user {
        background: #f5f5f5;
        color: #333;
        text-align: right;
        margin-left: auto;
    }
    .assistant {
        background-color: #f3f345;
        color: black;
        text-align: left;
    }
</style>