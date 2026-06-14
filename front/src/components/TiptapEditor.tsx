import { useCallback, useEffect, useRef, useState, forwardRef, useImperativeHandle, type ReactNode } from 'react'
import { useEditor, EditorContent, type Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import ImageExtension from '@tiptap/extension-image'
import LinkExtension from '@tiptap/extension-link'
import TableExtension from '@tiptap/extension-table'
import TableRow from '@tiptap/extension-table-row'
import TableCell from '@tiptap/extension-table-cell'
import TableHeader from '@tiptap/extension-table-header'
import TaskList from '@tiptap/extension-task-list'
import TaskItem from '@tiptap/extension-task-item'
import Underline from '@tiptap/extension-underline'
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight'
import { common, createLowlight } from 'lowlight'
import rehypeHighlight from 'rehype-highlight'
import { marked } from 'marked'
import TurndownService from 'turndown'
import ReactMarkdown from 'react-markdown'

// ── Turndown: HTML → Markdown ──

const turndown = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
  bulletListMarker: '-',
  emDelimiter: '*',
  strongDelimiter: '**',
})

// Custom rule: Tiptap task list items → GFM checklist syntax
turndown.addRule('taskListItem', {
  filter: (node) =>
    node.nodeName === 'LI' &&
    (node as HTMLElement).getAttribute('data-type') === 'taskItem',
  replacement: (content, node) => {
    const li = node as HTMLElement
    const checked = li.getAttribute('data-checked') === 'true'
    const text = content.replace(/<[^>]*>/g, '').trim()
    return `- [${checked ? 'x' : ' '}] ${text}\n`
  },
})

const lowlight = createLowlight(common)

// ── Types ──

export interface TiptapEditorHandle {
  scrollToHeading: (text: string, level: number) => void
}

interface TiptapEditorProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  onAutocomplete?: (context: string) => Promise<string | null>
}

// ── Toolbar sub-components ──

function ToolbarBtn({ onClick, active, label, title }: {
  onClick: () => void
  active?: boolean
  label: ReactNode
  title?: string
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      type="button"
      className={`top-bar-item${active ? ' is-active' : ''}`}
    >
      {label}
    </button>
  )
}

function EditorToolbar({ editor }: { editor: Editor | null }) {
  if (!editor) return null

  const headingLevels = [1, 2, 3, 4, 5, 6] as const
  const currentLevel = headingLevels.find(l =>
    editor.isActive('heading', { level: l })
  )
  const inTable = editor.isActive('table')
  const [showTableGrid, setShowTableGrid] = useState(false)
  const [tableGridHover, setTableGridHover] = useState({ rows: 0, cols: 0 })

  return (
    <div className="tiptap-toolbar">
      <div className="toolbar-inner">
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleBold().run()}
          active={editor.isActive('bold')}
          label={<strong>B</strong>}
          title="加粗"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleItalic().run()}
          active={editor.isActive('italic')}
          label={<em>I</em>}
          title="斜体"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleStrike().run()}
          active={editor.isActive('strike')}
          label={<span style={{textDecoration:'line-through'}}>S</span>}
          title="删除线"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleCode().run()}
          active={editor.isActive('code')}
          label="<>"
          title="行内代码"
        />
        <span className="toolbar-divider" />

        <select
          className="toolbar-select"
          title="标题级别"
          value={currentLevel ?? '0'}
          onChange={(e) => {
            const v = e.target.value
            if (v === '0') {
              editor.chain().focus().setParagraph().run()
            } else {
              editor.chain().focus().toggleHeading({ level: Number(v) as 1|2|3|4|5|6 }).run()
            }
          }}
        >
          <option value="0">正文</option>
          {headingLevels.map(l => (
            <option key={l} value={l}>H{l}</option>
          ))}
        </select>
        <span className="toolbar-divider" />

        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleBulletList().run()}
          active={editor.isActive('bulletList')}
          label="UL"
          title="无序列表"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
          active={editor.isActive('orderedList')}
          label="OL"
          title="有序列表"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleTaskList().run()}
          active={editor.isActive('taskList')}
          label="☑"
          title="任务列表"
        />
        <span className="toolbar-divider" />

        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
          active={editor.isActive('blockquote')}
          label={'"'}
          title="引用"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().toggleCodeBlock().run()}
          active={editor.isActive('codeBlock')}
          label="</>"
          title="代码块"
        />
        {editor.isActive('codeBlock') && (
          <select
            className="toolbar-select"
            value={editor.getAttributes('codeBlock').language || 'plaintext'}
            onChange={(e) => {
              editor.chain().focus().updateAttributes('codeBlock', { language: e.target.value }).run()
            }}
            title="编程语言"
          >
            <option value="plaintext">Plain Text</option>
            <option value="javascript">JavaScript</option>
            <option value="typescript">TypeScript</option>
            <option value="python">Python</option>
            <option value="html">HTML</option>
            <option value="css">CSS</option>
            <option value="json">JSON</option>
            <option value="xml">XML</option>
            <option value="bash">Bash</option>
            <option value="sql">SQL</option>
            <option value="go">Go</option>
            <option value="rust">Rust</option>
            <option value="java">Java</option>
            <option value="c">C</option>
            <option value="cpp">C++</option>
            <option value="php">PHP</option>
            <option value="ruby">Ruby</option>
            <option value="yaml">YAML</option>
            <option value="markdown">Markdown</option>
            <option value="dockerfile">Dockerfile</option>
            <option value="graphql">GraphQL</option>
          </select>
        )}

        <div style={{ position: 'relative' }}>
          <ToolbarBtn
            onClick={() => { if (inTable) return; setShowTableGrid((v) => !v) }}
            active={inTable}
            label="⊞"
            title={inTable ? '表格操作' : '插入表格'}
          />
          {showTableGrid && !inTable && (
            <div
              style={{ position: 'absolute', top: '100%', left: 0, zIndex: 50, background: 'var(--color-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}
              onMouseLeave={() => setShowTableGrid(false)}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                  {tableGridHover.rows > 0 ? `${tableGridHover.rows} × ${tableGridHover.cols}` : '选择表格大小'}
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8, 20px)', gap: 2 }}>
                {Array.from({ length: 8 }, (_, r) =>
                  Array.from({ length: 8 }, (_, c) => (
                    <div
                      key={`${r}-${c}`}
                      onMouseEnter={() => setTableGridHover({ rows: r + 1, cols: c + 1 })}
                      onClick={() => {
                        editor.chain().focus().insertTable({ rows: r + 1, cols: c + 1, withHeaderRow: true }).run()
                        setShowTableGrid(false)
                      }}
                      style={{
                        width: 20, height: 20,
                        border: '1px solid var(--color-border)',
                        borderRadius: 2,
                        background: r < tableGridHover.rows && c < tableGridHover.cols
                          ? 'var(--color-accent)' : 'var(--color-bg-secondary)',
                        cursor: 'pointer',
                        transition: 'background 0.1s',
                      }}
                    />
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {inTable && (
          <>
            <span className="toolbar-divider" />
            <ToolbarBtn
              onClick={() => editor.chain().focus().addColumnAfter().run()}
              label="+列"
              title="在右侧添加列"
            />
            <ToolbarBtn
              onClick={() => editor.chain().focus().addRowAfter().run()}
              label="+行"
              title="在下方添加行"
            />
            <ToolbarBtn
              onClick={() => editor.chain().focus().deleteColumn().run()}
              label="-列"
              title="删除当前列"
            />
            <ToolbarBtn
              onClick={() => editor.chain().focus().deleteRow().run()}
              label="-行"
              title="删除当前行"
            />
            <ToolbarBtn
              onClick={() => editor.chain().focus().deleteTable().run()}
              label="删表"
              title="删除整个表格"
            />
          </>
        )}
        <ToolbarBtn
          onClick={() => editor.chain().focus().setHorizontalRule().run()}
          label="—"
          title="分割线"
        />
        <span className="toolbar-divider" />

        <ToolbarBtn
          onClick={() => editor.chain().focus().undo().run()}
          label="↩"
          title="撤销"
        />
        <ToolbarBtn
          onClick={() => editor.chain().focus().redo().run()}
          label="↪"
          title="重做"
        />
      </div>
    </div>
  )
}

// ── Main component ──

const TiptapEditor = forwardRef<TiptapEditorHandle, TiptapEditorProps>(({ value, onChange, placeholder, onAutocomplete }, ref) => {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const onChangeRef = useRef(onChange)
  const autocompleteRef = useRef(onAutocomplete)
  const updatingRef = useRef(false)
  const ghostTextRef = useRef<string | null>(null)
  const ghostFromRef = useRef(0)
  const [preview, setPreview] = useState(false)
  const [ghost, setGhost] = useState<{ text: string; left: number; top: number } | null>(null)
  onChangeRef.current = onChange
  autocompleteRef.current = onAutocomplete

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4, 5, 6] },
        codeBlock: false,
      }),
      CodeBlockLowlight.configure({ lowlight }),
      Placeholder.configure({ placeholder }),
      ImageExtension,
      LinkExtension.configure({ openOnClick: false }),
      TableExtension.configure({ resizable: true }),
      TableRow,
      TableCell,
      TableHeader,
      TaskList,
      TaskItem.configure({ nested: true }),
      Underline,
    ],
    content: marked.parse(value || ''),
    onUpdate: ({ editor }) => {
      if (updatingRef.current) return
      const html = editor.getHTML()
      const md = turndown.turndown(html)
      onChangeRef.current(md)
    },
  })

  // Sync external value changes into the editor
  useEffect(() => {
    if (!editor) return
    const currentMd = turndown.turndown(editor.getHTML())
    if (currentMd === value) return
    updatingRef.current = true
    editor.commands.setContent(marked.parse(value || ''))
    updatingRef.current = false
  }, [value, editor])

  // Re-position ghost text (cursor may have moved)
  const updateGhostPosition = useCallback(() => {
    const g = ghostTextRef.current
    if (!g) { setGhost(null); return }
    if (!editor?.view) return
    try {
      const wrapper = wrapperRef.current
      if (!wrapper) return
      const coords = editor.view.coordsAtPos(ghostFromRef.current)
      if (!coords) return
      const rect = wrapper.getBoundingClientRect()
      setGhost({
        text: g,
        left: coords.left - rect.left,
        top: coords.top - rect.top,
      })
    } catch { /* ignore */ }
  }, [editor])

  // Autocomplete: 3 s after last keystroke
  useEffect(() => {
    if (!editor || !autocompleteRef.current) return
    ghostTextRef.current = null
    setGhost(null)
    const timer = setTimeout(async () => {
      const { from } = editor.state.selection
      const start = Math.max(0, from - 200)
      const context = editor.state.doc.textBetween(start, from)
      const posAtRequest = from
      try {
        const result = await autocompleteRef.current!(context)
        if (result) {
          ghostTextRef.current = result
          ghostFromRef.current = posAtRequest
          updateGhostPosition()
        }
      } catch { /* ignore */ }
    }, 3000)
    return () => { clearTimeout(timer) }
  }, [value, editor, updateGhostPosition])

  // Window scroll / resize → re-position ghost
  useEffect(() => {
    if (!ghostTextRef.current) return
    let rafId: number
    const onMove = () => {
      cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(updateGhostPosition)
    }
    window.addEventListener('scroll', onMove, { capture: true })
    window.addEventListener('resize', onMove)
    return () => {
      window.removeEventListener('scroll', onMove, { capture: true })
      window.removeEventListener('resize', onMove)
      cancelAnimationFrame(rafId)
    }
  }, [editor, ghost, updateGhostPosition])

  // Keyboard shortcuts (kept in direct DOM handler to match Milkdown behaviour)
  useEffect(() => {
    if (!editor) return
    const view = editor.view
    const handler = (e: KeyboardEvent) => {
      const isCtrl = e.ctrlKey || e.metaKey

      // Tab → accept ghost completion
      if (e.key === 'Tab' && ghostTextRef.current) {
        e.preventDefault()
        const { from } = view.state.selection
        view.dispatch(view.state.tr.insertText(ghostTextRef.current, from))
        ghostTextRef.current = null
        setGhost(null)
        return
      }

      // Ctrl+/ → toggle preview
      if (isCtrl && e.key === '/') {
        e.preventDefault()
        setPreview(p => !p)
        return
      }

      // Ctrl+1~6 → heading, toggle off if same level
      if (isCtrl && e.key >= '1' && e.key <= '6') {
        e.preventDefault()
        const level = parseInt(e.key) as 1|2|3|4|5|6
        if (editor.isActive('heading', { level })) {
          editor.chain().focus().setParagraph().run()
        } else {
          editor.chain().focus().toggleHeading({ level }).run()
        }
        return
      }

      // Ctrl+T → insert 3×3 table
      if (isCtrl && (e.key === 't' || e.key === 'T')) {
        e.preventDefault()
        editor.chain().focus().insertTable({ rows: 3, cols: 3 }).run()
        return
      }

      // Ctrl+L → select parent node
      if (isCtrl && (e.key === 'l' || e.key === 'L')) {
        e.preventDefault()
        editor.chain().focus().selectParentNode().run()
        return
      }

      // Ctrl+D → select word at cursor
      if (isCtrl && (e.key === 'd' || e.key === 'D')) {
        e.preventDefault()
        const { from, empty } = view.state.selection
        if (empty) {
          const doc = view.state.doc
          const textBefore = doc.textBetween(Math.max(0, from - 100), from)
          const textAfter = doc.textBetween(from, Math.min(doc.content.size, from + 100))
          const wordStart = (textBefore.match(/\w*$/) || [''])[0]
          const wordEnd = (textAfter.match(/^\w*/) || [''])[0]
          const start = from - wordStart.length
          const end = from + wordEnd.length
          if (start < end) {
            editor.commands.setTextSelection({ from: start, to: end })
          }
        }
        return
      }

      // Alt+Shift+5 → toggle strikethrough
      if (e.altKey && e.shiftKey && e.key === '5') {
        e.preventDefault()
        editor.chain().focus().toggleStrike().run()
        return
      }
    }

    view.dom.addEventListener('keydown', handler)
    return () => view.dom.removeEventListener('keydown', handler)
  }, [editor])

  useImperativeHandle(ref, () => ({
    scrollToHeading: (text: string, level: number) => {
      if (!editor) return
      const normalize = (s: string) => s.replace(/\\([.!\[\]()*_`~\-])/g, '$1')
      const { doc } = editor.state
      const target = normalize(text.trim().toLowerCase())
      doc.descendants((node, pos) => {
        if (node.type.name === 'heading' && node.attrs.level === level) {
          const nodeText = normalize(node.textContent.trim().toLowerCase())
          if (nodeText === target || nodeText.startsWith(target) || target.startsWith(nodeText)) {
            const headingEl = editor.view.nodeDOM(pos)
            if (headingEl instanceof HTMLElement) {
              headingEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }
            editor.commands.setTextSelection({ from: pos + 1, to: pos + 1 })
            editor.commands.focus()
            return false
          }
        }
        return true
      })
    },
  }), [editor])

  if (!editor) return null

  // ── Preview mode ──
  if (preview) {
    return (
      <div className="tiptap-wrapper h-full overflow-auto">
        <div className="max-w-3xl mx-auto px-10 py-10 prose prose-sm dark:prose-invert">
          <ReactMarkdown rehypePlugins={[rehypeHighlight]}>{value}</ReactMarkdown>
        </div>
      </div>
    )
  }

  return (
    <div ref={wrapperRef} className="tiptap-wrapper h-full relative flex flex-col">
      <EditorToolbar editor={editor} />
      <div className="flex-1 overflow-auto">
        <div className="max-w-3xl mx-auto">
          <EditorContent editor={editor} />
        </div>
        {ghost && (
          <div
            style={{
              position: 'absolute',
              left: ghost.left,
              top: ghost.top,
              pointerEvents: 'none',
              whiteSpace: 'pre',
              fontFamily: 'inherit',
              fontSize: 'inherit',
              lineHeight: 'inherit',
            }}
            className="text-[var(--color-text)] select-none"
          >
            <span style={{ opacity: 0.3 }}>{ghost.text}</span>
            <span className="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 text-[11px] font-medium rounded bg-[var(--color-accent-bg)] text-[var(--color-accent)]">
              Tab 补全
            </span>
          </div>
        )}
      </div>
    </div>
  )
})

export default TiptapEditor
