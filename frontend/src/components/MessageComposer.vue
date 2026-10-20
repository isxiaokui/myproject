<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'

const MAX_IMAGE_SIZE = 8 * 1024 * 1024
const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp']

const props = defineProps({
  modelValue: { type: String, required: true },
  loading: Boolean,
  identifying: Boolean,
  identifyStatus: { type: String, default: 'idle' },
})

const emit = defineEmits(['update:modelValue', 'send', 'identify-image'])
const fileInput = ref(null)
const textarea = ref(null)
const selectedImage = ref(null)
const imageTask = ref('flower')
const previewUrl = ref('')
const fileError = ref('')

watch(() => props.identifyStatus, (status) => {
  if (status === 'success') clearPreview()
})

const resizeTextarea = (element = textarea.value) => {
  if (!element) return
  element.style.height = 'auto'
  const maxHeight = Number.parseFloat(getComputedStyle(element).maxHeight)
  element.style.height = `${Math.min(element.scrollHeight, maxHeight)}px`
  element.style.overflowY = element.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

const handleInput = (event) => {
  emit('update:modelValue', event.target.value)
  resizeTextarea(event.target)
}

watch(() => props.modelValue, async () => {
  await nextTick()
  resizeTextarea()
})

const clearPreview = () => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  selectedImage.value = null
}

const chooseImage = () => fileInput.value?.click()

const handleFileChange = (event) => {
  const file = event.target.files?.[0]
  event.target.value = ''
  fileError.value = ''
  if (!file) return

  if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
    fileError.value = '仅支持 JPG、PNG 或 WebP 图片。'
    return
  }
  if (file.size > MAX_IMAGE_SIZE) {
    fileError.value = '图片不能超过 8 MB。'
    return
  }

  clearPreview()
  selectedImage.value = file
  previewUrl.value = URL.createObjectURL(file)
}

const identifyImage = () => {
  if (!selectedImage.value || props.loading) return
  emit('identify-image', {
    file: selectedImage.value,
    question: props.modelValue.trim(),
    task: imageTask.value,
  })
}

onUnmounted(clearPreview)
</script>

<template>
  <footer class="composer-area">
    <div v-if="previewUrl" class="upload-preview">
      <img :src="previewUrl" alt="待分析植物图片预览" />
      <div class="upload-preview-copy">
        <strong>图片已准备好</strong>
        <span>{{ selectedImage?.name }} · {{ (selectedImage.size / 1024 / 1024).toFixed(1) }} MB</span>
      </div>
      <button class="remove-image-button" type="button" aria-label="移除图片" :disabled="loading" @click="clearPreview">×</button>
    </div>

    <div class="composer">
      <input
        ref="fileInput"
        class="file-input"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        @change="handleFileChange"
      />
      <button
        class="image-button"
        type="button"
        :disabled="loading"
        aria-label="上传植物图片"
        title="上传植物图片（JPG、PNG、WebP，最大 8 MB）"
        @click="chooseImage"
      >
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="3.5" y="4.5" width="17" height="15" rx="3" stroke="currentColor" stroke-width="1.6" />
          <circle cx="9" cy="10" r="1.5" fill="currentColor" />
          <path d="m5 17 4.2-4.2a1.2 1.2 0 0 1 1.7 0l2 2 1.5-1.5a1.2 1.2 0 0 1 1.7 0L20 17" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
        </svg>
      </button>
      <select v-if="selectedImage" v-model="imageTask" class="image-task-select" :disabled="loading" aria-label="选择图片分析类型">
        <option value="flower">识别花卉</option>
        <option value="disease">诊断病虫害</option>
      </select>
      <textarea
        ref="textarea"
        class="chat-input"
        :value="modelValue"
        rows="1"
        maxlength="1000"
        :placeholder="selectedImage ? '补充描述（可选），然后开始分析…' : '问问花伴，关于花草的一切…'"
        :disabled="loading"
        @input="handleInput"
        @keydown.enter.exact.prevent="selectedImage ? identifyImage() : emit('send')"
      ></textarea>
      <button
        v-if="selectedImage"
        class="identify-button"
        type="button"
        :disabled="loading"
        @click="identifyImage"
      >
        {{ identifying ? '分析中…' : imageTask === 'disease' ? '诊断病虫害' : '识别花卉' }}
      </button>
      <button
        v-else
        class="send-button"
        type="button"
        :disabled="loading || !modelValue.trim()"
        :aria-label="loading ? '正在发送' : '发送消息'"
        @click="emit('send')"
      >
        <span v-if="!loading" aria-hidden="true">↑</span>
        <span v-else class="send-spinner" aria-hidden="true"></span>
      </button>
    </div>
    <p v-if="fileError" class="upload-error" role="alert">{{ fileError }}</p>
    <div class="composer-hint">
      <span><kbd>Enter</kbd> 发送</span>
      <span class="hint-dot">·</span>
      <span><kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span>
      <span class="disclaimer">花伴的建议仅供参考，特殊情况请咨询专业园艺师</span>
    </div>
  </footer>
</template>
