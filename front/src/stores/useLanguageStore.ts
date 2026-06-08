import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type Lang = 'zh-CN' | 'en-US'

interface LanguageState {
  lang: Lang
  setLang: (lang: Lang) => void
}

export const useLanguageStore = create<LanguageState>()(
  persist(
    (set) => ({
      lang: 'zh-CN',
      setLang: (lang) => set({ lang }),
    }),
    { name: 'language' }
  )
)
