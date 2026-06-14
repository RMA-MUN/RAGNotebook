import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { ArrowLeft, Save, Trash2, Download, Link2, ListTree, FileText, Users, GraduationCap, BookOpen, ListTodo, BookMarked, Plus, GripVertical } from 'lucide-react'
import TiptapEditor, { type TiptapEditorHandle } from '../components/TiptapEditor'
import TagInput from '../components/common/TagInput'
import RelatedFragments from '../components/note/RelatedFragments'
import OutlinePanel from '../components/note/OutlinePanel'
import { notesApi } from '../api/notes'
import { noteTemplatesApi } from '../api/noteTemplates'
import type { Note, NoteTemplate } from '../types/api'
import ConfirmDialog from '../components/common/ConfirmDialog'

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  FileText, Users, GraduationCap, BookOpen, ListTodo, BookMarked,
}

const CATEGORY_LABEL_MAP: Record<string, string> = {
  work: '工作', study: '学习', life: '生活', project: '技术', other: '其他',
}

const CATEGORIES = [
  { label: '工作', value: 'work' },
  { label: '学习', value: 'study' },
  { label: '生活', value: 'life' },
  { label: '技术', value: 'project' },
  { label: '其他', value: 'other' },
]
const DRAFT_KEY = 'note_draft'

interface Draft {
  title: string
  content: string
  tags?: string[]
  category?: string
}

function draftField<T>(id: string | undefined, key: keyof Draft, fallback: T): T {
  if (id && id !== 'new') return fallback
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) return fallback
    return (JSON.parse(raw)?.[key] ?? fallback) as T
  } catch {
    return fallback
  }
}

export default function NoteEditor() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [title, setTitle] = useState(() => draftField<string>(id, 'title', ''))
  const [content, setContent] = useState(() => draftField<string>(id, 'content', ''))
  const [category, setCategory] = useState(() => draftField<string>(id, 'category', ''))
  const [tags, setTags] = useState<string[]>(() => draftField<string[]>(id, 'tags', []))
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showDelete, setShowDelete] = useState(false)
  const [showRelated, setShowRelated] = useState(false)
  const [showOutline, setShowOutline] = useState(false)
  const [showTemplatePicker, setShowTemplatePicker] = useState(false)
  const [showTemplateManager, setShowTemplateManager] = useState(false)
  const [templateName, setTemplateName] = useState('')
  const [editingTemplate, setEditingTemplate] = useState<NoteTemplate | null>(null)
  const [showNewTemplateForm, setShowNewTemplateForm] = useState(false)
  const [showSaveAsTemplate, setShowSaveAsTemplate] = useState(false)
  const templateApplied = useRef(false)
  const editorRef = useRef<TiptapEditorHandle>(null)
  const isNew = !id || id === 'new'

  useEffect(() => {
    if (isNew || !id) return
    setLoading(true)
    notesApi.get(id).then((res) => {
      const note = res.data as Note
      setTitle(note.title)
      setContent(note.content)
      setCategory(note.category || '')
      setTags(note.tags || [])
    }).catch(() => {}).finally(() => setLoading(false))
  }, [id, isNew])

  const autoSave = useCallback(() => {
    if (isNew) {
      const draft: Draft = { title, content, tags, category }
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
    }
  }, [title, content, tags, category, isNew])

  useEffect(() => {
    const timer = setTimeout(autoSave, 2000)
    return () => clearTimeout(timer)
  }, [autoSave])

  const handleSave = async () => {
    if (!title.trim() && !content.trim()) return
    setSaving(true)
    try {
      if (isNew) {
        const res = await notesApi.create({ title, content, category: category || undefined, tags })
        localStorage.removeItem(DRAFT_KEY)
        navigate(`/notes/${(res.data as Note).id}`, { replace: true })
      } else if (id) {
        await notesApi.update(id, { title, content, category, tags })
        toast.success('保存成功')
      }
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!id) return
    try {
      await notesApi.delete(id)
      navigate('/notes')
    } catch {
      toast.error('删除失败')
    }
  }

  const handleDownload = async () => {
    if (!id) return
    try {
      const blob = await notesApi.download(id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${title || 'note'}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error('下载失败')
    }
  }

  const applyTemplate = (tpl: NoteTemplate) => {
    setTitle(tpl.title)
    setContent(tpl.content)
    setCategory(tpl.category || '')
    setTags(tpl.tags || [])
    setShowTemplatePicker(false)
    templateApplied.current = true
  }

  const handleSaveAsTemplate = async () => {
    if (!templateName.trim()) return
    try {
      await noteTemplatesApi.create({
        name: templateName.trim(),
        category,
        title,
        content,
        tags,
      })
      toast.success('模板已保存')
      setShowSaveAsTemplate(false)
      setTemplateName('')
      refreshTemplates()
    } catch {
      toast.error('保存模板失败')
    }
  }

  const refreshTemplates = () => {
    noteTemplatesApi.list().then((res) => {
      const list = (res.data as NoteTemplate[]) || []
      setTemplates(list)
      const order = loadTemplateOrder()
      if (order) {
        const map = new Map(list.map((t) => [t.id, t]))
        const ordered = order.map((id) => map.get(id)).filter(Boolean) as NoteTemplate[]
        const rest = list.filter((t) => !order.includes(t.id))
        setTemplateItems([...ordered, ...rest])
      } else {
        setTemplateItems(list)
      }
    }).catch(() => {})
  }

  const startEditTemplate = (tpl: NoteTemplate) => {
    setEditingTemplate(tpl)
    setEditForm({
      name: tpl.name,
      title: tpl.title,
      content: tpl.content,
      category: tpl.category || '',
      tags: (tpl.tags || []).join(', '),
    })
  }

  const handleUpdateTemplate = async () => {
    if (!editingTemplate) return
    try {
      await noteTemplatesApi.update(editingTemplate.id, {
        name: editForm.name,
        title: editForm.title,
        content: editForm.content,
        category: editForm.category,
        tags: editForm.tags.split(',').map((t) => t.trim()).filter(Boolean),
      })
      toast.success('模板已更新')
      setEditingTemplate(null)
      refreshTemplates()
    } catch {
      toast.error('更新失败')
    }
  }

  const handleDeleteTemplate = async (tpl: NoteTemplate) => {
    if (tpl.is_default) return
    try {
      await noteTemplatesApi.delete(tpl.id)
      toast.success('已删除')
      refreshTemplates()
    } catch {
      toast.error('删除失败')
    }
  }

  const handleTemplateDragStart = (index: number) => {
    dragItem.current = index
  }

  const handleTemplateDragOver = (index: number) => {
    setDragOverIndex(index)
  }

  const handleTemplateDrop = async () => {
    const from = dragItem.current
    dragItem.current = null
    setDragOverIndex(null)
    if (from === null) return
    const reordered = [...templateItems]
    const [moved] = reordered.splice(from, 1)
    if (!moved) return
    reordered.splice(dragOverIndex ?? reordered.length, 0, moved)
    setTemplateItems(reordered)
    const ids = reordered.map((t) => t.id)
    saveTemplateOrder(ids)
    try {
      await noteTemplatesApi.reorder(ids)
    } catch {
      toast.error('排序保存失败')
    }
  }

  const handleCreateTemplate = async () => {
    if (!newTemplateForm.name.trim()) return
    try {
      await noteTemplatesApi.create({
        name: newTemplateForm.name.trim(),
        title: newTemplateForm.title,
        content: newTemplateForm.content,
        category: newTemplateForm.category,
        tags: newTemplateForm.tags.split(',').map((t) => t.trim()).filter(Boolean),
      })
      toast.success('模板已创建')
      setShowNewTemplateForm(false)
      setNewTemplateForm({ name: '', title: '', content: '', category: '', tags: '' })
      refreshTemplates()
    } catch {
      toast.error('创建失败')
    }
  }

  const handleSaveRef = useRef(handleSave)
  handleSaveRef.current = handleSave

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSaveRef.current()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-5 h-5 border-2 border-[var(--color-border)] border-t-[var(--color-accent)] rounded-full animate-spin" />
      </div>
    )
  }

  if (showTemplatePicker) {
    return (
      <div className="h-full flex flex-col bg-[var(--color-bg)]">
        <header className="flex items-center flex-shrink-0 h-11 px-6 border-b border-[var(--color-border-light)]">
          <button
            onClick={() => navigate('/notes')}
            className="flex items-center justify-center w-8 h-8 text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] rounded-lg transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
          <span className="ml-3 text-sm font-medium text-[var(--color-text)]">选择笔记模板</span>
          <button
            onClick={() => setShowTemplateManager(true)}
            className="ml-auto px-3 py-1 text-xs rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors"
          >
            管理模板
          </button>
        </header>
        <div className="flex-1 overflow-auto p-8">
          <div className="max-w-2xl mx-auto grid grid-cols-2 gap-4">
            {templates?.map((tpl) => {
              const Icon = ICON_MAP[tpl.icon] || FileText
              return (
                <button
                  key={tpl.id}
                  onClick={() => applyTemplate(tpl)}
                  className="flex flex-col items-start p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)] hover:shadow-sm transition-all text-left group"
                >
                  <div className="flex items-center gap-3 mb-2">
                    <div className="w-9 h-9 rounded-lg bg-[var(--color-bg-secondary)] flex items-center justify-center text-[var(--color-text-secondary)] group-hover:text-[var(--color-accent)] group-hover:bg-[var(--color-accent-bg)] transition-colors">
                      <Icon size={18} />
                    </div>
                    <span className="text-sm font-medium text-[var(--color-text)]">{tpl.name}</span>
                  </div>
                  {tpl.category && (
                    <span className="text-xs text-[var(--color-text-tertiary)]">{CATEGORY_LABEL_MAP[tpl.category] || tpl.category}</span>
                  )}
                  {tpl.content && (
                    <p className="text-xs text-[var(--color-text-tertiary)] mt-2 line-clamp-2 leading-relaxed">{tpl.content.slice(0, 80)}...</p>
                  )}
                </button>
              )
            })}
          </div>
        </div>

        {showTemplateManager && (
          <>
            <div className="fixed inset-0 bg-black/40 z-50" onClick={() => { setShowTemplateManager(false); setEditingTemplate(null); setShowNewTemplateForm(false) }} />
            <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-[var(--color-card)] rounded-lg shadow-xl w-[600px] max-w-[90vw] max-h-[80vh] flex flex-col">
              <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)]">
                <h3 className="text-base font-medium text-[var(--color-text)]">管理模板</h3>
                <div className="flex items-center gap-2">
                  {!editingTemplate && !showNewTemplateForm && (
                    <button
                      onClick={() => { setShowNewTemplateForm(true); setEditingTemplate(null) }}
                      className="px-3 py-1 text-xs rounded-md bg-[var(--color-accent)] text-white hover:opacity-90"
                    >
                      + 新建模板
                    </button>
                  )}
                  <button onClick={() => { setShowTemplateManager(false); setEditingTemplate(null); setShowNewTemplateForm(false) }} className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text)]">✕</button>
                </div>
              </div>
              <div className="flex-1 overflow-auto p-6">
                {showNewTemplateForm ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-2">
                      <button onClick={() => setShowNewTemplateForm(false)} className="p-1 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)]">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>
                      </button>
                      <h4 className="text-sm font-medium text-[var(--color-text)]">新建模板</h4>
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">名称 *</label>
                      <input type="text" value={newTemplateForm.name} onChange={(e) => setNewTemplateForm((f) => ({ ...f, name: e.target.value }))} placeholder="模板名称" className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">默认标题</label>
                      <input type="text" value={newTemplateForm.title} onChange={(e) => setNewTemplateForm((f) => ({ ...f, title: e.target.value }))} placeholder="笔记默认标题" className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">分类</label>
                      <select value={newTemplateForm.category} onChange={(e) => setNewTemplateForm((f) => ({ ...f, category: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]">
                        <option value="">无</option>
                        <option value="work">工作</option>
                        <option value="study">学习</option>
                        <option value="life">生活</option>
                        <option value="project">技术</option>
                        <option value="other">其他</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">标签（逗号分隔）</label>
                      <input type="text" value={newTemplateForm.tags} onChange={(e) => setNewTemplateForm((f) => ({ ...f, tags: e.target.value }))} placeholder="标签1, 标签2" className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">默认内容（Markdown）</label>
                      <textarea value={newTemplateForm.content} onChange={(e) => setNewTemplateForm((f) => ({ ...f, content: e.target.value }))} rows={10} placeholder={"## 标题\n\n内容..."} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] font-mono placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] resize-y" />
                    </div>
                    <div className="flex justify-end gap-2 pt-2">
                      <button onClick={() => setShowNewTemplateForm(false)} className="px-4 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]">返回</button>
                      <button onClick={handleCreateTemplate} disabled={!newTemplateForm.name.trim()} className="px-4 py-1.5 text-sm rounded-md bg-[var(--color-accent)] text-white disabled:opacity-40">创建模板</button>
                    </div>
                  </div>
                ) : editingTemplate ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-2">
                      <button onClick={() => setEditingTemplate(null)} className="p-1 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)]">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>
                      </button>
                      <h4 className="text-sm font-medium text-[var(--color-text)]">编辑模板</h4>
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">名称</label>
                      <input type="text" value={editForm.name} onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">默认标题</label>
                      <input type="text" value={editForm.title} onChange={(e) => setEditForm((f) => ({ ...f, title: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">分类</label>
                      <select value={editForm.category} onChange={(e) => setEditForm((f) => ({ ...f, category: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]">
                        <option value="">无</option>
                        <option value="work">工作</option>
                        <option value="study">学习</option>
                        <option value="life">生活</option>
                        <option value="project">技术</option>
                        <option value="other">其他</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">标签（逗号分隔）</label>
                      <input type="text" value={editForm.tags} onChange={(e) => setEditForm((f) => ({ ...f, tags: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
                    </div>
                    <div>
                      <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">默认内容（Markdown）</label>
                      <textarea value={editForm.content} onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))} rows={10} className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] font-mono focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] resize-y" />
                    </div>
                    <div className="flex justify-end gap-2 pt-2">
                      <button onClick={() => setEditingTemplate(null)} className="px-4 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]">返回</button>
                      <button onClick={handleUpdateTemplate} className="px-4 py-1.5 text-sm rounded-md bg-[var(--color-accent)] text-white">保存修改</button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {templateItems.length === 0 && (
                      <p className="text-sm text-[var(--color-text-tertiary)] text-center py-8">暂无模板</p>
                    )}
                    {templateItems.map((tpl, index) => (
                      <div
                        key={tpl.id}
                        draggable
                        onDragStart={() => handleTemplateDragStart(index)}
                        onDragOver={(e) => { e.preventDefault(); handleTemplateDragOver(index) }}
                        onDrop={(e) => { e.preventDefault(); handleTemplateDrop() }}
                        onDragEnd={() => setDragOverIndex(null)}
                        className={`flex items-center justify-between p-3 rounded-lg border border-[var(--color-border)] transition-colors ${
                          dragOverIndex === index
                            ? 'border-t-2 border-t-[var(--color-accent)] bg-[var(--color-bg-secondary)]'
                            : 'hover:border-[var(--color-accent)]'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <GripVertical size={14} className="text-[var(--color-text-tertiary)] shrink-0 cursor-grab active:cursor-grabbing" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-[var(--color-text)]">{tpl.name}</span>
                              {tpl.is_default && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[var(--color-text-tertiary)]">内置</span>
                              )}
                            </div>
                            {tpl.content && (
                              <p className="text-xs text-[var(--color-text-tertiary)] mt-1 truncate">{tpl.content.slice(0, 60)}</p>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-3">
                          <button onClick={() => startEditTemplate(tpl)} className="px-2 py-1 text-xs rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]">编辑</button>
                          {!tpl.is_default && (
                            <button onClick={() => handleDeleteTemplate(tpl)} className="px-2 py-1 text-xs rounded text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)]">删除</button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col bg-[var(--color-bg)]">
      {/* ====== Top bar ====== */}
      <header className="flex items-center justify-between flex-shrink-0 h-11 px-6 border-b border-[var(--color-border-light)]">
        <button
          onClick={() => navigate('/notes')}
          className="flex items-center justify-center w-8 h-8 text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] rounded-lg transition-colors"
          title="返回"
        >
          <ArrowLeft size={18} />
        </button>

        {isNew && saveStatus === 'saved' && (
          <span className="text-xs text-[var(--color-text-tertiary)] ml-3 select-none">草稿已保存</span>
        )}

        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowOutline((v) => !v)}
            className={`flex items-center justify-center w-8 h-8 rounded-lg transition-colors ${
              showOutline
                ? 'text-[var(--color-accent)] bg-[var(--color-accent-bg)]'
                : 'text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)]'
            }`}
            title="目录"
          >
            <ListTree size={16} />
          </button>
          <span className="w-px h-5 bg-[var(--color-border-light)] mx-0.5" />
          {!isNew && (
            <button
              onClick={() => setShowRelated((v) => !v)}
              className={`flex items-center justify-center w-8 h-8 rounded-lg transition-colors ${
                showRelated
                  ? 'text-[var(--color-accent)] bg-[var(--color-accent-bg)]'
                  : 'text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)]'
              }`}
              title="关联片段"
            >
              <Link2 size={16} />
            </button>
          )}
          {!isNew && (
            <button
              onClick={handleDownload}
              className="flex items-center justify-center w-8 h-8 text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] rounded-lg transition-colors"
              title={t('note.download')}
            >
              <Download size={16} />
            </button>
          )}
          {!isNew && (
            <button
              onClick={() => setShowDelete(true)}
              className="flex items-center justify-center w-8 h-8 text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)] rounded-lg transition-colors"
              title={t('note.delete')}
            >
              <Trash2 size={16} />
            </button>
          )}
          {!isNew && (
            <button
              onClick={() => setShowSaveAsTemplate(true)}
              className="flex items-center justify-center w-8 h-8 text-[var(--color-text-tertiary)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-bg)] rounded-lg transition-colors"
              title="存为模板"
            >
              <Plus size={16} />
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 h-8 text-sm font-medium rounded-lg bg-[var(--color-accent)] text-white hover:opacity-90 disabled:opacity-40 transition-all ml-1"
          >
            <Save size={15} />
            {saving ? '保存中' : t('note.save')}
          </button>
        </div>
      </header>

      <div className="flex-1 flex min-h-0">
        <OutlinePanel
          content={content}
          open={showOutline}
          onClose={() => setShowOutline(false)}
          onHeadingClick={(text, level) => editorRef.current?.scrollToHeading(text, level)}
        />
        <div className="flex flex-col flex-1 min-w-0">
          {/* ====== Title ====== */}
          <div className="flex-shrink-0 px-10 pt-10 pb-4">
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="未命名笔记"
              className="w-full text-[30px] font-bold font-heading leading-tight tracking-tight text-[var(--color-text)] bg-transparent border-none outline-none placeholder:text-[var(--color-text-placeholder)]"
            />
          </div>

          {/* ====== Category pills + Tags ====== */}
          <div className="flex-shrink-0 px-10 pb-6">
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-1">
                {CATEGORIES.map((cat) => (
                  <button
                    key={cat.value}
                    onClick={() => setCategory(category === cat.value ? '' : cat.value)}
                    className={`px-3 py-1 text-xs rounded-full font-medium transition-all ${
                      category === cat.value
                        ? 'bg-[var(--color-accent)] text-white shadow-sm'
                        : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text)]'
                    }`}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
              <div className="flex-1 min-w-[180px]">
                <TagInput tags={tags} onChange={setTags} placeholder="添加标签..." />
              </div>
            </div>
          </div>

          {/* ====== Crepe WYSIWYG Editor ====== */}
          <div className="flex-1 min-h-0">
            <TiptapEditor
              ref={editorRef}
              key={id || 'new'}
              value={content}
              onChange={setContent}
              placeholder="开始写作..."
              onAutocomplete={async (context) => {
                try {
                  const res = await notesApi.autocomplete(context)
                  return (res.data as { completion?: string })?.completion || null
                } catch {
                  return null
                }
              }}
            />
          </div>
        </div>

        {id && (
          <RelatedFragments
            noteId={id}
            open={showRelated}
            onClose={() => setShowRelated(false)}
          />
        )}
      </div>

      <ConfirmDialog
        open={showDelete}
        onOpenChange={setShowDelete}
        title={t('note.delete')}
        message={t('note.deleteConfirm')}
        variant="danger"
        confirmText={t('note.delete')}
        onConfirm={handleDelete}
      />

      {showSaveAsTemplate && (
        <>
          <div className="fixed inset-0 bg-black/40 z-50" onClick={() => setShowSaveAsTemplate(false)} />
          <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-[var(--color-card)] rounded-lg shadow-xl p-6 w-[400px] max-w-[90vw]">
            <h3 className="text-base font-medium text-[var(--color-text)] mb-4">保存为模板</h3>
            <input
              type="text"
              value={templateName}
              onChange={(e) => setTemplateName(e.target.value)}
              placeholder="输入模板名称"
              className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') handleSaveAsTemplate() }}
            />
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setShowSaveAsTemplate(false)}
                className="px-4 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]"
              >
                取消
              </button>
              <button
                onClick={handleSaveAsTemplate}
                disabled={!templateName.trim()}
                className="px-4 py-1.5 text-sm rounded-md bg-[var(--color-accent)] text-white disabled:opacity-40"
              >
                保存
              </button>
            </div>
          </div>
        </>
      )}

    </div>
  )
}
