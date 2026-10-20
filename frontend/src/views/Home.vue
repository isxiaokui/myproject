<script setup>
import ChatHeader from '../components/ChatHeader.vue'
import AuthPanel from '../components/AuthPanel.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import MessageComposer from '../components/MessageComposer.vue'
import MessageList from '../components/MessageList.vue'
import WelcomePanel from '../components/WelcomePanel.vue'
import { useChat } from '../composables/useChat'

const {
  input,
  messages,
  loading,
  identifying,
  identifyStatus,
  conversation,
  conversations,
  currentConversationId,
  historyLoading,
  historyError,
  authToken,
  authUser,
  authLoading,
  authError,
  sendMessage,
  askSuggestion,
  identifyFlower,
  startNewConversation,
  selectConversation,
  removeConversation,
  authenticate,
  logout,
} = useChat()
</script>

<template>
  <main class="page-shell">
    <div v-if="authLoading && authToken" class="auth-loading">正在载入你的会话…</div>
    <AuthPanel
      v-else-if="!authToken"
      :loading="authLoading"
      :error="authError"
      @submit="authenticate"
    />
    <div v-else class="workspace-layout">
      <ConversationSidebar
        :conversations="conversations"
        :current-conversation-id="currentConversationId"
        :loading="loading"
        :history-loading="historyLoading"
        :error="historyError"
        @new="startNewConversation"
        @select="selectConversation"
        @delete="removeConversation"
      />
      <section class="chat-card" aria-label="花伴智能助手">
        <ChatHeader
          :has-messages="messages.length > 0"
          :loading="loading"
          :username="authUser?.username || ''"
          @clear="startNewConversation"
          @logout="logout"
        />

        <section ref="conversation" class="conversation" aria-live="polite">
          <WelcomePanel
            v-if="messages.length === 0"
            :loading="loading"
            @select-suggestion="askSuggestion"
          />
          <MessageList v-else :messages="messages" :loading="loading" />
        </section>

        <MessageComposer
          v-model="input"
          :loading="loading"
          :identifying="identifying"
          :identify-status="identifyStatus"
          @send="sendMessage"
          @identify-image="identifyFlower"
        />
      </section>
    </div>
  </main>
</template>
