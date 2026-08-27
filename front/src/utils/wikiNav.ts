import type { NavigateFunction } from 'react-router-dom'
import { graphApi } from '../api/graph'

let navigateFn: NavigateFunction | null = null
let installed = false

export function setWikiNavigator(fn: NavigateFunction) {
  navigateFn = fn
}

/** 全局捕获 a[data-wiki] 点击：解析 [[目标]] → 跳转笔记页或图谱页。捕获阶段先于 ProseMirror/其他处理器。 */
export function installWikiLinkClickHandler() {
  if (installed || typeof document === 'undefined') return
  installed = true
  document.addEventListener(
    'click',
    (e) => {
      const el = (e.target as HTMLElement | null)?.closest?.('a[data-wiki]') as HTMLAnchorElement | null
      if (!el) return
      e.preventDefault()
      const raw = (el.getAttribute('href') || '').replace(/^#wiki:/, '')
      let name = raw
      try {
        name = decodeURIComponent(raw)
      } catch {
        /* 非法 % 序列时原样使用 */
      }
      void navigateWikiTarget(name)
    },
    true,
  )
}

/** 解析双链目标：精确命中笔记标题 → 跳编辑器；命中实体 → 图谱页选中该实体；都未命中 → 图谱页搜索该名。 */
export async function navigateWikiTarget(name: string) {
  if (!navigateFn) return
  try {
    const res = await graphApi.search(name)
    const note = res?.notes?.find((n) => n.title === name)
    if (note) {
      navigateFn(`/notes/${note.id}`)
      return
    }
    const entity = res?.entities?.find((en) => en.name === name)
    navigateFn(entity ? `/graph?entity=${entity.id}` : `/graph?entity=${encodeURIComponent(name)}`)
  } catch {
    /* 搜索失败静默，不打断用户 */
  }
}