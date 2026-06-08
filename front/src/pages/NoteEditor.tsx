import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Save, Trash2, Download, Link2, ListTree } from 'lucide-react'
import TiptapEditor, { type TiptapEditorHandle } from '../components/TiptapEditor'
import TagInput from '../components/common/TagInput'
import RelatedFragments from '../components/note/RelatedFragments'
import OutlinePanel from '../components/note/OutlinePanel'
import { notesApi } from '../api/notes'
import type { Note } from '../types/api'
import ConfirmDialog from '../components/common/ConfirmDialog'

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
      }
    } catch { /* ignore */ } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!id) return
    try {
      await notesApi.delete(id)
      navigate('/notes')
    } catch { /* ignore */ }
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
    } catch { /* ignore */ }
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
    </div>
  )
}
