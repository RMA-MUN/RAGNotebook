import { Node } from '@tiptap/core'

export const WikiLink = Node.create({
  name: 'wikiLink',
  group: 'inline',
  inline: true,
  selectable: true,
  atom: true,

  addAttributes() {
    return {
      target: {
        default: null,
        parseHTML: (element) => (element.getAttribute('href') || '').replace(/^#wiki:/, ''),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'a[data-wiki]', priority: 100 }]
  },

  renderHTML({ node }) {
    return ['a', { 'data-wiki': 'true', href: `#wiki:${node.attrs.target}` }, `[[${node.attrs.target}]]`]
  },

  addKeyboardShortcuts() {
    return {
      'ArrowRight': () => false,
    }
  },
})

export function renderWikiLinkText(target: string): string {
  return `[[${target}]]`
}