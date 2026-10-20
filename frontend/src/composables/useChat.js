import { nextTick, onMounted, ref, watch } from 'vue'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:3001').replace(/\/+$/, '')
const CHAT_ENDPOINT = `${API_BASE_URL}/chat/stream`
const IDENTIFY_ENDPOINT = `${API_BASE_URL}/identify-flower`
const DIAGNOSE_ENDPOINT = `${API_BASE_URL}/diagnose-plant`
const CONVERSATIONS_ENDPOINT = `${API_BASE_URL}/conversations`
const AUTH_ENDPOINT = `${API_BASE_URL}/auth`
const REQUEST_TIMEOUT_MS = 60_000
const CHAT_TIMEOUT_MS = 180_000
const MAX_CONTEXT_MESSAGES = 20
const IDENTIFY_TIMEOUT_MS = 90_000

const fetchWithTimeout = async (url, options, timeoutMs = REQUEST_TIMEOUT_MS) => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('请求超时，请稍后重试。')
    throw error
  } finally {
    clearTimeout(timeout)
  }
}

const readSseStream = async (response, onEvent) => {
  if (!response.body) throw new Error('浏览器无法读取流式响应。')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatchEvents = async (final = false) => {
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) await onEvent(data === '[DONE]' ? { type: 'stream_end' } : JSON.parse(data))
      boundary = buffer.indexOf('\n\n')
    }

    if (final && buffer.trim()) {
      const data = buffer
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) await onEvent(data === '[DONE]' ? { type: 'stream_end' } : JSON.parse(data))
      buffer = ''
    }
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      await dispatchEvents(done)
      if (done) break
    }
  } finally {
    reader.releaseLock()
  }
}

const jsonRequest = async (url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) => {
  const response = await fetchWithTimeout(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  }, timeoutMs)
  const body = await response.text()
  let result
  try {
    if (!body.trim()) throw new SyntaxError('empty response body')
    result = JSON.parse(body)
  } catch {
    const message = response.ok
      ? '服务器返回了空响应或无效数据，请稍后重试。'
      : `服务器返回了无效错误响应（HTTP ${response.status}），请检查后端服务和 /api 代理。`
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  if (!response.ok) {
    const error = new Error(result?.detail || `请求失败（HTTP ${response.status}），请稍后重试。`)
    error.status = response.status
    throw error
  }
  return result
}

const messageContentForApi = (message) => {
  if (typeof message.content === 'string') return message.content
  const result = message.identification
  const diagnosis = message.diagnosis
  if (!result && !diagnosis) return ''

  if (diagnosis) {
    return [
      diagnosis.summary,
      ...(diagnosis.issues || []).map((issue) => `- ${issue.name}（${issue.confidence}把握）：${issue.evidence}`),
      ...(diagnosis.recommendations || []).map((recommendation) => `建议：${recommendation}`),
      diagnosis.clarifying_question,
    ].filter(Boolean).join('\n')
  }

  const candidates = (result.candidates || []).map((candidate) =>
    `- ${candidate.name}（${candidate.confidence}把握）：${candidate.evidence}`,
  )
  const knowledge = (result.knowledge || []).flatMap((item) =>
    item.passages.map((passage) => `${item.flower}养护资料：${passage}`),
  )
  return [
    `图片识别结果${result.uncertain ? '（仍需确认）' : ''}：${result.summary}`,
    ...candidates,
    ...knowledge,
    result.clarifying_question,
  ].filter(Boolean).join('\n')
}

const isWeatherQuestion = (text) => /天气|气温|温度|下雨|降雨|降雪|天气预报/.test(text)

const hasExplicitCity = (text) => {
  if (/我在[\u4e00-\u9fa5]{2,8}(?:市|区|县)?/.test(text)) return true

  const normalized = text
    .trim()
    .replace(/^(?:(?:麻烦|请问|请|帮我|我想知道|我想查|帮忙)\s*)+/, '')
    .replace(/^(?:查询|查一下|查查|查|看看|看一下)\s*/, '')
    .replace(/^(?:一下|今天|明天|现在|当前)\s*/, '')

  return /^[\u4e00-\u9fa5]{2,8}(?:市|区|县|州|盟)?(?:(?:今天|明天|现在|当前|的|会不会|会)){0,2}(?:天气|气温|温度|下雨|降雨|降雪)/.test(normalized)
}

const requestCurrentLocation = () => new Promise((resolve) => {
  if (!navigator.geolocation) {
    resolve(null)
    return
  }

  navigator.geolocation.getCurrentPosition(
    ({ coords }) => resolve({ latitude: coords.latitude, longitude: coords.longitude }),
    () => resolve(null),
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
  )
})

export function useChat() {
  const input = ref('')
  const messages = ref([])
  const loading = ref(false)
  const identifying = ref(false)
  const identifyStatus = ref('idle')
  const conversation = ref(null)
  const conversations = ref([])
  const currentConversationId = ref(null)
  const historyLoading = ref(false)
  const historyError = ref('')
  const authToken = ref(localStorage.getItem('florassistant-token') || '')
  const authUser = ref(null)
  const authLoading = ref(false)
  const authError = ref('')

  const authHeaders = () => authToken.value
    ? { Authorization: `Bearer ${authToken.value}` }
    : {}

  const authorizedRequest = async (url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) => {
    try {
      return await jsonRequest(url, {
        ...options,
        headers: { ...options.headers, ...authHeaders() },
      }, timeoutMs)
    } catch (error) {
      if (error?.status === 401) logout()
      throw error
    }
  }

  const scrollToLatest = async () => {
    await nextTick()
    if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
  }

  watch([() => messages.value.length, loading], scrollToLatest)

  const refreshConversations = async () => {
    const result = await authorizedRequest(CONVERSATIONS_ENDPOINT)
    conversations.value = result.items || []
    historyError.value = ''
  }

  const createConversation = async (title = '新对话') => {
    const result = await authorizedRequest(CONVERSATIONS_ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ title }),
    })
    currentConversationId.value = result.id
    messages.value = []
    await refreshConversations().catch((error) => {
      historyError.value = error instanceof Error ? error.message : '会话已创建，但历史列表未能更新。'
    })
    return result.id
  }

  const ensureConversation = async (title) => {
    if (currentConversationId.value) return currentConversationId.value
    return createConversation(title)
  }

  const startNewConversation = async () => {
    if (loading.value) return
    historyError.value = ''
    try {
      await createConversation()
      await scrollToLatest()
    } catch (error) {
      historyError.value = error instanceof Error ? error.message : '无法新建会话。'
    }
  }

  const selectConversation = async (id) => {
    if (loading.value || id === currentConversationId.value) return
    historyLoading.value = true
    historyError.value = ''
    try {
      const result = await authorizedRequest(`${CONVERSATIONS_ENDPOINT}/${encodeURIComponent(id)}`)
      currentConversationId.value = result.id
      messages.value = (result.messages || []).map((message) => ({
        role: message.role,
        content: message.content,
        ...(message.identification ? { identification: message.identification } : {}),
        ...(message.diagnosis ? { diagnosis: message.diagnosis } : {}),
        ...(message.isPartial ? { isPartial: true } : {}),
        ...(message.isError ? { isError: true } : {}),
      }))
      await scrollToLatest()
    } catch (error) {
      historyError.value = error instanceof Error ? error.message : '无法载入会话。'
      await refreshConversations().catch(() => {})
    } finally {
      historyLoading.value = false
    }
  }

  const removeConversation = async (id) => {
    if (loading.value) return
    historyError.value = ''
    try {
      await authorizedRequest(`${CONVERSATIONS_ENDPOINT}/${encodeURIComponent(id)}`, { method: 'DELETE' })
      await refreshConversations()
      if (currentConversationId.value === id) {
        currentConversationId.value = null
        messages.value = []
        if (conversations.value.length) await selectConversation(conversations.value[0].id)
      }
    } catch (error) {
      historyError.value = error instanceof Error ? error.message : '无法删除会话。'
    }
  }

  const sendMessage = async () => {
    const content = input.value.trim()
    if (loading.value || !content) return

    loading.value = true
    historyError.value = ''
    let assistantMessage = null
    let timeout = null
    try {
      const conversationId = await ensureConversation(content)
      messages.value.push({ role: 'user', content })
      input.value = ''
      await scrollToLatest()

      let location = null
      let locationUnavailable = false
      if (isWeatherQuestion(content) && !hasExplicitCity(content)) {
        location = await requestCurrentLocation()
        locationUnavailable = !location
      }

      const requestMessages = messages.value
        .slice(-MAX_CONTEXT_MESSAGES)
        .map(({ role, ...message }) => ({ role, content: messageContentForApi(message) }))
      assistantMessage = { role: 'assistant', content: '' }
      messages.value.push(assistantMessage)

      const controller = new AbortController()
      timeout = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS)
      const response = await fetch(CHAT_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          msg: requestMessages,
          conversation_id: conversationId,
          location,
          location_unavailable: locationUnavailable,
        }),
        signal: controller.signal,
      })
      if (!response.ok) {
        const result = await response.json().catch(() => ({}))
        if (response.status === 401) logout()
        throw new Error(result.detail || '请求失败，请稍后重试。')
      }

      let receivedDone = false
      let receivedTerminator = false
      let streamError = null
      await readSseStream(response, async (event) => {
        if (event.type === 'token') {
          assistantMessage.content += event.text || ''
          await scrollToLatest()
        } else if (event.type === 'done') {
          receivedDone = true
          currentConversationId.value = event.conversation_id || conversationId
        } else if (event.type === 'stream_end') {
          receivedTerminator = true
        } else if (event.type === 'error') {
          streamError = new Error(event.message || '生成回答时发生错误。')
        }
      })
      if (streamError) throw streamError
      if (!receivedDone || !receivedTerminator) throw new Error('连接中断，回答尚未完整生成。')
    } catch (error) {
      const errorMessage = error?.name === 'AbortError'
        ? '请求超时，请稍后重试。'
        : error instanceof Error ? error.message : '请求失败，请检查网络或后端服务。'
      if (assistantMessage) {
        assistantMessage.isPartial = Boolean(assistantMessage.content)
        assistantMessage.content = assistantMessage.content
          ? `${assistantMessage.content}\n\n${errorMessage}`
          : errorMessage
        assistantMessage.isError = true
      } else {
        messages.value.push({ role: 'assistant', content: errorMessage, isError: true })
      }
    } finally {
      clearTimeout(timeout)
      loading.value = false
      await refreshConversations().catch(() => {})
      await scrollToLatest()
    }
  }

  const askSuggestion = (text) => {
    input.value = text
    sendMessage()
  }

  const identifyFlower = async ({ file, question, task = 'flower' }) => {
    if (loading.value || !file) return

    loading.value = true
    identifying.value = true
    identifyStatus.value = 'loading'
    historyError.value = ''
    try {
      const conversationId = await ensureConversation(question?.trim() || (task === 'disease' ? '病虫害诊断' : '图片识花'))
      const taskLabel = task === 'disease' ? '病虫害诊断' : '识别花卉'
      messages.value.push({
        role: 'user',
        content: question ? `请${taskLabel}这张图片：${question}` : `请${taskLabel}这张图片。`,
      })
      await scrollToLatest()

      const formData = new FormData()
      formData.append('image', file)
      formData.append('question', question || '')
      formData.append('conversation_id', conversationId)

      const endpoint = task === 'disease' ? DIAGNOSE_ENDPOINT : IDENTIFY_ENDPOINT
      const response = await fetchWithTimeout(endpoint, {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      }, IDENTIFY_TIMEOUT_MS)
      const result = await response.json()
      if (!response.ok) {
        if (response.status === 401) logout()
        throw new Error(result.detail || '图片识别失败，请稍后重试。')
      }

      messages.value.push({
        role: 'assistant',
        content: messageContentForApi(task === 'disease' ? { diagnosis: result } : { identification: result }),
        ...(task === 'disease' ? { diagnosis: result } : { identification: result }),
      })
      currentConversationId.value = result.conversation_id
      input.value = ''
      identifyStatus.value = 'success'
    } catch (error) {
      identifyStatus.value = 'error'
      messages.value.push({
        role: 'assistant',
        content: error instanceof Error ? error.message : '图片分析失败，请检查网络或后端服务。',
        isError: true,
      })
    } finally {
      loading.value = false
      identifying.value = false
      await refreshConversations().catch(() => {})
      await scrollToLatest()
    }
  }

  const authenticate = async ({ username, password, mode }) => {
    authLoading.value = true
    authError.value = ''
    try {
      const result = await jsonRequest(`${AUTH_ENDPOINT}/${mode}`, {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      authToken.value = result.token
      authUser.value = result.user
      localStorage.setItem('florassistant-token', result.token)
      historyLoading.value = true
      await refreshConversations()
      if (conversations.value.length) await selectConversation(conversations.value[0].id)
    } catch (error) {
      authError.value = error instanceof Error ? error.message : '登录失败，请稍后重试。'
    } finally {
      authLoading.value = false
      historyLoading.value = false
    }
  }

  const logout = () => {
    authToken.value = ''
    authUser.value = null
    authError.value = ''
    conversations.value = []
    currentConversationId.value = null
    messages.value = []
    localStorage.removeItem('florassistant-token')
  }

  onMounted(async () => {
    if (!authToken.value) return
    authLoading.value = true
    historyLoading.value = true
    try {
      authUser.value = await authorizedRequest(`${AUTH_ENDPOINT}/me`)
      await refreshConversations()
      if (conversations.value.length) await selectConversation(conversations.value[0].id)
    } catch (error) {
      if (error?.status !== 401) {
        historyError.value = error instanceof Error ? error.message : '无法载入历史会话。'
      }
    } finally {
      historyLoading.value = false
      authLoading.value = false
    }
  })

  return {
    input, messages, loading, identifying, identifyStatus, conversation,
    conversations, currentConversationId, historyLoading, historyError,
    authToken, authUser, authLoading, authError,
    authenticate, logout,
    sendMessage, askSuggestion, identifyFlower, startNewConversation,
    selectConversation, removeConversation,
  }
}
