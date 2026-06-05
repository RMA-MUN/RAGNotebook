<template>
  <div class="note-editor-layout">
    <!-- ====== 左侧 编辑器主区域 ====== -->
    <div class="editor-main" :class="{ 'editor-main--full': !sidebarVisible && !isNew }">
      <!-- 顶部操作栏 -->
      <div class="editor-toolbar">
        <span class="toolbar-back" @click="goBack">← 返回</span>
        <span class="toolbar-title-label">{{ isNew ? '新建笔记' : '编辑笔记' }}</span>
        <div class="toolbar-actions">
          <span class="toolbar-btn" @click="handleSave">{{ saving ? '保存中...' : '保存' }}</span>
          <span v-if="!isNew" class="toolbar-btn toolbar-btn--delete" @click="handleDelete">删除</span>
          <span v-if="!isNew" class="toolbar-btn toolbar-btn--download" @click="handleDownload">下载</span>
        </div>
      </div>

      <!-- 笔记标题栏 -->
      <div class="title-bar">
        <input
          v-model="title"
          class="title-input"
          placeholder="输入笔记标题..."
          maxlength="200"
        />
        <div v-if="tags.length > 0 || category" class="title-meta">
          <span v-if="category" class="title-category">{{ categoryMap[category] || category }}</span>
          <TagBadge v-for="t in tags" :key="t" :tag="t" :color="getCategoryColor(category)" />
        </div>
      </div>

      <!-- 快捷 Markdown 工具栏 -->
      <QuickToolbar :editor-ref="markdownEditorRef" />

      <!-- Markdown 编辑器 -->
      <div class="editor-body">
        <MarkdownEditor ref="markdownEditorRef" v-model="content" />
        <InlineCompletion
          :context="completionContext"
          :position="cursorPosition"
          @accept="handleAccept"
        />
      </div>
    </div>

    <!-- ====== 右侧 关联笔记侧边栏 ====== -->
    <div class="sidebar-zone" v-if="!isNew">
      <!-- 折叠态：仅显示一条竖线 + 展开按钮 -->
      <div v-if="!sidebarVisible" class="sidebar-collapsed" @click="toggleSidebar">
        <div class="sidebar-toggle-btn">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </div>
        <div class="sidebar-toggle-hint">相关</div>
      </div>

      <!-- 展开态：完整侧边栏 -->
      <div v-else class="related-sidebar">
        <!-- 正在查看某篇关联笔记的详情 -->
        <template v-if="expandedNote">
          <div class="related-header">
            <span class="sidebar-back-btn" @click="expandedNote = null">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
              返回列表
            </span>
            <span class="sidebar-close-btn" @click="toggleSidebar">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
            </span>
          </div>
          <div class="related-detail" v-if="detailLoading">
            <van-loading size="20" />
          </div>
          <div v-else class="related-detail">
            <h3 class="detail-title">{{ expandedNote.title }}</h3>
            <div v-if="expandedNote.category || (expandedNote.tags && expandedNote.tags.length > 0)" class="detail-tags">
              <span v-if="expandedNote.category" class="title-category">{{ categoryMap[expandedNote.category] || expandedNote.category }}</span>
              <TagBadge v-for="t in expandedNote.tags || []" :key="t" :tag="t" />
            </div>
            <div class="detail-body markdown-body" v-html="renderedExpandedContent"></div>
          </div>
        </template>

        <!-- 默认：列表视图 -->
        <template v-else>
          <div class="related-header">
            <span>相关笔记</span>
            <span class="sidebar-close-btn" @click="toggleSidebar">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
            </span>
          </div>

          <div v-if="loadingRelated" class="related-loading">
            <van-loading size="20" />
          </div>

          <div v-else-if="relatedItems.length === 0" class="related-empty">
            暂无相关笔记
          </div>

          <div v-else class="related-list">
            <div
              v-for="item in relatedItems"
              :key="item.id"
              class="related-card card-shadow"
              @click="expandRelatedNote(item)"
            >
              <div class="related-card-header">
                <span class="related-source" :class="item.source === 'note' ? 'src-note' : 'src-kb'">
                  {{ item.source === 'note' ? '笔记' : '知识库' }}
                </span>
                <span class="related-similarity">{{ (item.similarity * 100).toFixed(0) }}%</span>
              </div>
              <h4 class="related-card-title ellipsis">{{ item.title }}</h4>
              <p class="related-card-preview">{{ item.content_preview }}</p>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * NoteEditor 笔记编辑器 —— 双栏布局，右侧关联推荐可折叠。
 * 支持内容变化后自动刷新关联推荐（防抖 3 秒）。
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { apiConfig } from '../config/api'
import { useUserStore } from '../store/user'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import QuickToolbar from '../components/QuickToolbar.vue'
import TagBadge from '../components/TagBadge.vue'
import InlineCompletion from '../components/InlineCompletion.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** ---- 编辑器引用 ---- */
const markdownEditorRef = ref(null)

/** ---- 内联补全（行内自动补全） ---- */
const completionContext = ref('')
const cursorPosition = ref({ top: 0, left: 0 })
let cmCleanup = null

/** ---- 编辑器状态 ---- */
const title = ref('')
const content = ref('')
const tags = ref([])
const category = ref('')
const saving = ref(false)
const noteId = ref('')

/** ---- 侧边栏状态 ---- */
const sidebarVisible = ref(false)
const relatedItems = ref([])
const loadingRelated = ref(false)
const expandedNote = ref(null)   /** 当前在侧边栏内展开查看的笔记详情 */
const detailLoading = ref(false)  /** 展开笔记详情的加载状态 */
const renderedExpandedContent = ref('')  /** 渲染为 HTML 的笔记内容 */

let relatedRefreshTimer = null

/** 是否为新建 */
const isNew = computed(() => route.params.id === 'new')

/** 分类映射 */
const categoryMap = { work: '工作', study: '学习', life: '生活', project: '项目' }

const token = computed(() => userStore.token)
function getHeaders() {
  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token.value}`,
  }
}

let autoSaveTimer = null

function getCategoryColor(cat) {
  const map = { work: 'work', study: 'study', life: 'life', project: 'project' }
  return map[cat] || 'default'
}

/** 切换侧边栏展开/折叠 */
function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value
  if (sidebarVisible.value && relatedItems.value.length === 0) {
    fetchRelated()
  }
}

/** 在侧边栏内展开查看关联笔记的完整内容 */
async function expandRelatedNote(item) {
  // 知识库文档：直接展示检索返回的完整切片内容，无需再请求 API
  if (item.source === 'knowledge_base') {
    expandedNote.value = {
      id: item.id,
      title: item.title,
      tags: [],
      category: '',
    }
    const html = await marked.parse(item.content || item.content_preview || '')
    renderedExpandedContent.value = DOMPurify.sanitize(html)
    return
  }

  // 笔记：通过 API 查询完整内容
  detailLoading.value = true
  expandedNote.value = { id: item.id, title: item.title }
  renderedExpandedContent.value = ''

  try {
    const res = await fetch(apiConfig.endpoints.noteDetail(item.id), {
      headers: getHeaders(),
    })
    const json = await res.json()
    if (json.code === 200 && json.data) {
      expandedNote.value = json.data
      const html = await marked.parse(json.data.content || '')
      renderedExpandedContent.value = DOMPurify.sanitize(html)
    }
  } catch (e) {
    showToast('加载笔记失败')
    expandedNote.value = null
  } finally {
    detailLoading.value = false
  }
}

/** 保存草稿到 localStorage（防抖 2 秒） */
function autoSaveDraft() {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(() => {
    localStorage.setItem('note_draft', JSON.stringify({
      title: title.value,
      content: content.value,
      noteId: noteId.value,
      timestamp: Date.now(),
    }))
  }, 2000)
}

function clearDraft() {
  localStorage.removeItem('note_draft')
}

/** 加载笔记详情 */
async function loadNote() {
  try {
    const res = await fetch(apiConfig.endpoints.noteDetail(noteId.value), {
      headers: getHeaders(),
    })
    const json = await res.json()
    if (json.code === 200 && json.data) {
      title.value = json.data.title
      content.value = json.data.content
      tags.value = json.data.tags || []
      category.value = json.data.category || ''
    }
  } catch (e) {
    showToast('加载笔记失败')
  }
}

/** 加载关联推荐 */
async function fetchRelated() {
  if (!noteId.value || isNew.value) return
  loadingRelated.value = true
  try {
    const res = await fetch(apiConfig.endpoints.noteRelated(noteId.value), {
      headers: getHeaders(),
    })
    const json = await res.json()
    if (json.code === 200) {
      relatedItems.value = json.data || []
    }
  } catch (e) {
    // ignore
  } finally {
    loadingRelated.value = false
  }
}

/** 内容变化后防抖刷新关联推荐（仅在侧边栏展开时） */
function scheduleRelatedRefresh() {
  if (isNew.value || !noteId.value) return
  if (relatedRefreshTimer) clearTimeout(relatedRefreshTimer)
  relatedRefreshTimer = setTimeout(() => {
    fetchRelated()
  }, 3000)
}

/** 保存笔记 */
async function handleSave() {
  if (!title.value.trim() && !content.value.trim()) {
    showToast('标题或内容不能为空')
    return
  }

  saving.value = true
  try {
    if (isNew.value) {
      const res = await fetch(apiConfig.endpoints.noteCreate, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ title: title.value, content: content.value }),
      })
      const json = await res.json()
      if (json.code === 200) {
        clearDraft()
        showToast('保存成功')
        router.replace(`/notes/${json.data.id}`)
      } else {
        showToast('保存失败')
      }
    } else {
      const res = await fetch(apiConfig.endpoints.noteUpdate(noteId.value), {
        method: 'PUT',
        headers: getHeaders(),
        body: JSON.stringify({ title: title.value, content: content.value }),
      })
      const json = await res.json()
      if (json.code === 200) {
        clearDraft()
        showToast('保存成功')
      } else {
        showToast('保存失败')
      }
    }
  } catch (e) {
    showToast('网络错误')
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  try {
    await showConfirmDialog({
      title: '确认删除',
      message: '删除后无法恢复，确定要删除吗？',
    })
    const res = await fetch(apiConfig.endpoints.noteDelete(noteId.value), {
      method: 'DELETE',
      headers: getHeaders(),
    })
    const json = await res.json()
    if (json.code === 200) {
      showToast('删除成功')
      router.replace('/notes')
    }
  } catch (e) {
    // 用户取消
  }
}

/** 下载笔记为 Markdown 文件 */
async function handleDownload() {
  try {
    const res = await fetch(apiConfig.endpoints.noteDownload(noteId.value), {
      headers: { 'Authorization': `Bearer ${token.value}` },
    })
    const contentType = res.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      const json = await res.json()
      showToast(json.message || '下载失败')
      return
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${title.value || '笔记'}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    showToast('下载成功')
  } catch (e) {
    showToast('下载失败')
  }
}

/** 接受行内补全：将补全文本插入光标位置 */
function handleAccept(completion) {
  const cm = markdownEditorRef.value?.getEditorCm()
  if (!cm) return
  const cursor = cm.getCursor()
  cm.getDoc().replaceRange(completion, cursor)
  completionContext.value = ''
}

function goBack() {
  if (title.value || content.value) {
    autoSaveDraft()
    showToast('草稿已保存')
  }
  router.push('/notes')
}

/** 监听内容变化：触发自动保存草稿 + 防抖刷新关联推荐 */
watch([title, content], () => {
  autoSaveDraft()
  scheduleRelatedRefresh()
})

onMounted(() => {
  noteId.value = route.params.id
  if (isNew.value) {
    clearDraft()
  } else {
    loadNote()
  }
  setupCursorTracking()
})

/** 设置光标跟踪：监听光标位置变化，更新上下文和 ghost text 位置 */
function setupCursorTracking() {
  const cm = markdownEditorRef.value?.getEditorCm()
  if (!cm) {
    setTimeout(setupCursorTracking, 100)
    return
  }

  const editorBody = document.querySelector('.editor-body')

  function updateCursor() {
    if (!editorBody) return
    const ctx = markdownEditorRef.value?.getCursorContext()
    if (!ctx) return
    completionContext.value = ctx.textBeforeCursor
    const rect = editorBody.getBoundingClientRect()
    cursorPosition.value = {
      top: ctx.cursorCoords.top - rect.top,
      left: ctx.cursorCoords.left - rect.left,
    }
  }

  cm.on('cursorActivity', updateCursor)
  const scrollEl = cm.getScrollerElement()
  if (scrollEl) scrollEl.addEventListener('scroll', updateCursor)

  updateCursor()

  cmCleanup = () => {
    cm.off('cursorActivity', updateCursor)
    if (scrollEl) scrollEl.removeEventListener('scroll', updateCursor)
  }
}

onUnmounted(() => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  if (relatedRefreshTimer) clearTimeout(relatedRefreshTimer)
  if (cmCleanup) cmCleanup()
})
</script>

<style scoped>
/* ============ 整体双栏布局 ============ */
.note-editor-layout {
  display: flex;
  height: 100vh;
  background: #f5f5f5;
  overflow: hidden;
}

/* ============ 左侧编辑器主区域 ============ */
.editor-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  transition: margin-right 0.25s ease;
}
.editor-main--full {
  /* 侧边栏折叠时编辑器撑满 */
}

/* 顶部操作栏 */
.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  height: 48px;
  background: #fff;
  border-bottom: 1px solid #ebedf0;
  flex-shrink: 0;
  position: relative;
  z-index: 20;
}
.toolbar-back {
  font-size: 14px;
  color: #666;
  cursor: pointer;
  user-select: none;
}
.toolbar-back:hover {
  color: var(--van-primary-color, #D4914A);
}
.toolbar-title-label {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}
.toolbar-actions {
  display: flex;
  gap: 16px;
}
.toolbar-btn {
  font-size: 14px;
  color: var(--van-primary-color, #D4914A);
  cursor: pointer;
  user-select: none;
  padding: 4px 0;
}
.toolbar-btn:hover {
  opacity: 0.8;
}
.toolbar-btn--delete {
  color: #ee0a24;
}
.toolbar-btn--download {
  color: #1989fa;
}

/* 标题栏 */
.title-bar {
  padding: 16px 24px 12px;
  background: #fff;
  border-bottom: 1px solid #ebedf0;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}
.title-input {
  width: 100%;
  border: none;
  outline: none;
  font-size: 22px;
  font-weight: 700;
  font-family: 'Noto Sans SC', sans-serif;
  color: #1a1a1a;
  line-height: 1.4;
  background: transparent;
  position: relative;
  z-index: 1;
}
.title-input::placeholder {
  color: #bbb;
}
.title-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.title-category {
  font-size: 12px;
  color: #999;
  padding: 2px 8px;
  background: #f0f0f0;
  border-radius: 4px;
}

/* 编辑器主体 */
.editor-body {
  flex: 1;
  overflow-y: auto;
  background: #fff;
  position: relative;
  min-height: 0;
  z-index: 1;
}

/* ============ 侧边栏区域 ============ */
.sidebar-zone {
  position: relative;
  flex-shrink: 0;
}

/* 折叠态：竖条 + 展开按钮 */
.sidebar-collapsed {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 100%;
  background: #fff;
  border-left: 1px solid #e0e0e0;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
}
.sidebar-collapsed:hover {
  background: #f5f0e8;
}
.sidebar-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  color: #999;
}
.sidebar-collapsed:hover .sidebar-toggle-btn {
  color: var(--van-primary-color, #D4914A);
}
.sidebar-toggle-hint {
  margin-top: 6px;
  font-size: 11px;
  color: #999;
  writing-mode: vertical-rl;
  letter-spacing: 2px;
}

/* 展开态：完整侧边栏 */
.related-sidebar {
  width: 320px;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
  border-left: 1px solid #e0e0e0;
  animation: slideIn 0.2s ease;
}
@keyframes slideIn {
  from { width: 0; opacity: 0; }
  to { width: 320px; opacity: 1; }
}
.related-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 12px;
  font-size: 15px;
  font-weight: 700;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}
.sidebar-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  cursor: pointer;
  color: #999;
}
.sidebar-close-btn:hover {
  background: #f0f0f0;
  color: #333;
}
.sidebar-back-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  font-weight: 500;
  color: var(--van-primary-color, #D4914A);
  cursor: pointer;
  user-select: none;
}
.sidebar-back-btn:hover {
  opacity: 0.8;
}
.related-loading {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}
.related-empty {
  padding: 60px 20px;
  text-align: center;
  font-size: 14px;
  color: #bbb;
}
.related-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
}
.related-card {
  padding: 14px 16px;
  margin-bottom: 12px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.2s;
}
.related-card:hover {
  background: #f0f0f0;
}
.related-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.related-source {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
  font-weight: 500;
}
.src-note {
  background: #E8F0FE;
  color: #1967D2;
}
.src-kb {
  background: #FEF7E0;
  color: #B06000;
}
.related-similarity {
  font-size: 12px;
  color: #bbb;
}
.related-card-title {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 600;
  color: #333;
}
.related-card-preview {
  margin: 0;
  font-size: 12px;
  color: #999;
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

/* ============ 关联笔记详情视图 ============ */
.related-detail {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
.detail-title {
  margin: 0 0 10px;
  font-size: 17px;
  font-weight: 700;
  color: #1a1a1a;
  line-height: 1.4;
}
.detail-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
}
.detail-body {
  font-size: 14px;
  color: #333;
  line-height: 1.8;
}
/* markdown-body 在侧边栏内的适配 */
.detail-body :deep(h1),
.detail-body :deep(h2),
.detail-body :deep(h3) {
  margin-top: 14px;
  margin-bottom: 8px;
  font-weight: 600;
  color: #1a1a1a;
}
.detail-body :deep(p) {
  margin: 0 0 8px;
}
.detail-body :deep(pre) {
  background: #f5f5f5;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12px;
}
.detail-body :deep(code) {
  background: #f0f0f0;
  padding: 2px 4px;
  border-radius: 3px;
  font-size: 12px;
}
.detail-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
.detail-body :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid #ddd;
  color: #666;
}
.detail-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}
.detail-body :deep(th),
.detail-body :deep(td) {
  border: 1px solid #e0e0e0;
  padding: 6px 8px;
  font-size: 12px;
}
.detail-body :deep(img) {
  max-width: 100%;
}

/* ============ 响应式：窄屏时隐藏侧边栏 ============ */
@media (max-width: 768px) {
  .sidebar-zone {
    display: none;
  }
}
</style>
