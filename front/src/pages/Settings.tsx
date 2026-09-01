import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Sun, Moon, Languages, Loader2, Eye, EyeOff } from 'lucide-react'
import { toast } from 'sonner'
import { useThemeStore } from '../stores/useThemeStore'
import { useLanguageStore } from '../stores/useLanguageStore'
import i18n from '../i18n'
import { aiConfigApi } from '../api/aiConfig'
import type { AIConfig, CapabilityPayload } from '../types/api'

const EMPTY_CAP: CapabilityPayload = { base_url: '', api_key: '', model: '' }
const WEB_EMPTY = { enabled: false, api_key: '', provider: '' }

interface CapabilityCardProps {
  title: string
  value: CapabilityPayload
  onChange: (next: CapabilityPayload) => void
  apiKeySet?: boolean
  hint?: string
}

function CapabilityCard({ title, value, onChange, apiKeySet, hint }: CapabilityCardProps) {
  const { t } = useTranslation()
  const [showKey, setShowKey] = useState(false)

  const inputClass =
    'w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]'

  return (
    <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-[var(--color-text)]">{title}</p>
        {apiKeySet && <span className="text-xs text-[var(--color-accent)]">{t('settings.ai.configured')}</span>}
      </div>
      <div>
        <label className="text-xs text-[var(--color-text-secondary)] block mb-1">{t('settings.ai.baseUrl')}</label>
        <input
          type="text"
          value={value.base_url}
          onChange={(e) => onChange({ ...value, base_url: e.target.value })}
          className={inputClass}
        />
      </div>
      <div>
        <label className="text-xs text-[var(--color-text-secondary)] block mb-1">{t('settings.ai.apiKey')}</label>
        <div className="relative">
          <input
            type={showKey ? 'text' : 'password'}
            value={value.api_key}
            onChange={(e) => onChange({ ...value, api_key: e.target.value })}
            placeholder={t('settings.ai.keyHint')}
            className={`${inputClass} pr-11`}
          />
          <button
            type="button"
            onClick={() => setShowKey((s) => !s)}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-secondary)] hover:text-[var(--color-accent)]"
            aria-label={t(showKey ? 'settings.ai.hideKey' : 'settings.ai.showKey')}
          >
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
      </div>
      <div>
        <label className="text-xs text-[var(--color-text-secondary)] block mb-1">{t('settings.ai.model')}</label>
        <input
          type="text"
          value={value.model}
          onChange={(e) => onChange({ ...value, model: e.target.value })}
          className={inputClass}
        />
      </div>
      {hint && <p className="text-xs text-[var(--color-text-tertiary)]">{hint}</p>}
    </div>
  )
}

export default function Settings() {
  const { t } = useTranslation()
  const { theme, setTheme } = useThemeStore()
  const { lang, setLang } = useLanguageStore()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [chat, setChat] = useState<CapabilityPayload>(EMPTY_CAP)
  const [embed, setEmbed] = useState<CapabilityPayload>(EMPTY_CAP)
  const [vision, setVision] = useState<CapabilityPayload>(EMPTY_CAP)
  const [rerank, setRerank] = useState<CapabilityPayload>(EMPTY_CAP)
  const [web, setWeb] = useState<{ enabled: boolean; api_key: string; provider: string }>(WEB_EMPTY)
  const [keySet, setKeySet] = useState({ chat: false, embed: false, vision: false, rerank: false, web: false })
  const [chatKeyEdited, setChatKeyEdited] = useState(false)
  const [embedKeyEdited, setEmbedKeyEdited] = useState(false)
  const [visionKeyEdited, setVisionKeyEdited] = useState(false)
  const [rerankKeyEdited, setRerankKeyEdited] = useState(false)
  const [webKeyEdited, setWebKeyEdited] = useState(false)
  const [showWebKey, setShowWebKey] = useState(false)

  // eslint-disable-next-line react-hooks/set-state-in-effect -- 仅挂载时加载一次配置，语言切换不得重建
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const cfg: AIConfig = await aiConfigApi.get()
        if (cancelled) return
        setChat({ base_url: cfg.chat.base_url, api_key: '', model: cfg.chat.model })
        setEmbed({ base_url: cfg.embed.base_url, api_key: '', model: cfg.embed.model })
        setVision({ base_url: cfg.vision.base_url, api_key: '', model: cfg.vision.model })
        setRerank({ base_url: cfg.rerank.base_url, api_key: '', model: cfg.rerank.model })
        setWeb({ enabled: cfg.web_search.enabled, api_key: '', provider: cfg.web_search.provider })
        setKeySet({
          chat: cfg.chat.api_key_set,
          embed: cfg.embed.api_key_set,
          vision: cfg.vision.api_key_set,
          rerank: cfg.rerank.api_key_set,
          web: cfg.web_search.api_key_set,
        })
      } catch {
        if (!cancelled) toast.error(t('settings.ai.loadFailed'))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const handleLangChange = (newLang: 'zh-CN' | 'en-US') => {
    setLang(newLang)
    i18n.changeLanguage(newLang)
  }

  const handleChatChange = (next: CapabilityPayload) => {
    if (next.api_key !== chat.api_key) setChatKeyEdited(true)
    setChat(next)
  }

  const handleEmbedChange = (next: CapabilityPayload) => {
    if (next.api_key !== embed.api_key) setEmbedKeyEdited(true)
    setEmbed(next)
  }

  const handleVisionChange = (next: CapabilityPayload) => {
    if (next.api_key !== vision.api_key) setVisionKeyEdited(true)
    setVision(next)
  }

  const handleRerankChange = (next: CapabilityPayload) => {
    if (next.api_key !== rerank.api_key) setRerankKeyEdited(true)
    setRerank(next)
  }

  const handleWebChange = (next: { enabled: boolean; api_key: string; provider: string }) => {
    if (next.api_key !== web.api_key) setWebKeyEdited(true)
    setWeb(next)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await aiConfigApi.save({
        chat: { ...chat, api_key: chatKeyEdited ? chat.api_key : undefined },
        embed: { ...embed, api_key: embedKeyEdited ? embed.api_key : undefined },
        vision: { ...vision, api_key: visionKeyEdited ? vision.api_key : undefined },
        rerank: { ...rerank, api_key: rerankKeyEdited ? rerank.api_key : undefined },
        web_search: { ...web, api_key: webKeyEdited ? web.api_key : undefined },
      })
      toast.success(t('settings.ai.saved'))
    } catch {
      toast.error(t('settings.ai.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const listInputClass =
    'w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]'

  return (
    <div className="max-w-2xl mx-auto py-8 px-6">
      <h1 className="font-heading text-xl font-semibold text-[var(--color-text)] mb-8">{t('settings.title')}</h1>

      <div className="space-y-6">
        <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {theme === 'light' ? <Sun size={18} className="text-[var(--color-text-secondary)]" /> : <Moon size={18} className="text-[var(--color-text-secondary)]" />}
              <div>
                <p className="text-sm font-medium text-[var(--color-text)]">{t('settings.theme')}</p>
                <p className="text-xs text-[var(--color-text-tertiary)]">{t(theme === 'light' ? 'settings.light' : 'settings.dark')}</p>
              </div>
            </div>
            <button
              onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
              className={`relative w-12 h-6 rounded-full transition-colors ${theme === 'dark' ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-bg-tertiary)]'}`}
            >
              <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow-sm transition-transform ${theme === 'dark' ? 'translate-x-6' : 'translate-x-0.5'}`} />
            </button>
          </div>
        </div>

        <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Languages size={18} className="text-[var(--color-text-secondary)]" />
              <div>
                <p className="text-sm font-medium text-[var(--color-text)]">{t('settings.language')}</p>
                <p className="text-xs text-[var(--color-text-tertiary)]">{lang === 'zh-CN' ? '中文' : 'English'}</p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => handleLangChange('zh-CN')}
                className={`px-3 py-1.5 text-xs rounded-md transition-colors ${lang === 'zh-CN' ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]' : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]'}`}
              >
                中文
              </button>
              <button
                onClick={() => handleLangChange('en-US')}
                className={`px-3 py-1.5 text-xs rounded-md transition-colors ${lang === 'en-US' ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]' : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]'}`}
              >
                English
              </button>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="animate-spin mr-2" size={20} />
            <span className="text-sm text-[var(--color-text-secondary)]">{t('common.loading')}</span>
          </div>
        ) : (
          <div className="space-y-4">
            <h2 className="font-heading text-lg font-semibold text-[var(--color-text)]">{t('settings.ai.title')}</h2>

            <CapabilityCard title={t('settings.ai.chat')} value={chat} onChange={handleChatChange} apiKeySet={keySet.chat} />
            <CapabilityCard title={t('settings.ai.embed')} value={embed} onChange={handleEmbedChange} apiKeySet={keySet.embed} hint={t('settings.ai.reindexHint')} />
            <CapabilityCard title={t('settings.ai.vision')} value={vision} onChange={handleVisionChange} apiKeySet={keySet.vision} />
            <CapabilityCard title={t('settings.ai.rerank')} value={rerank} onChange={handleRerankChange} apiKeySet={keySet.rerank} />

            <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-4 space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-[var(--color-text)]">{t('settings.ai.web')}</p>
                <div className="flex items-center gap-2">
                  {keySet.web && <span className="text-xs text-[var(--color-accent)]">{t('settings.ai.configured')}</span>}
                  <button
                    type="button"
                    onClick={() => handleWebChange({ ...web, enabled: !web.enabled })}
                    aria-label={t('settings.ai.enabled')}
                    className={`relative w-10 h-5 rounded-full transition-colors ${web.enabled ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-bg-tertiary)]'}`}
                  >
                    <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${web.enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
                  </button>
                </div>
              </div>
              <div>
                <label className="text-xs text-[var(--color-text-secondary)] block mb-1">{t('settings.ai.provider')}</label>
                <input
                  type="text"
                  value={web.provider}
                  onChange={(e) => handleWebChange({ ...web, provider: e.target.value })}
                  className={listInputClass}
                />
              </div>
              <div>
                <label className="text-xs text-[var(--color-text-secondary)] block mb-1">{t('settings.ai.apiKey')}</label>
                <div className="relative">
                  <input
                    type={showWebKey ? 'text' : 'password'}
                    value={web.api_key}
                    onChange={(e) => handleWebChange({ ...web, api_key: e.target.value })}
                    placeholder={t('settings.ai.keyHint')}
                    className={`${listInputClass} pr-11`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowWebKey((s) => !s)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-secondary)] hover:text-[var(--color-accent)]"
                    aria-label={t(showWebKey ? 'settings.ai.hideKey' : 'settings.ai.showKey')}
                  >
                    {showWebKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
            </div>

            <div className="flex justify-end">
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white disabled:opacity-50 flex items-center"
              >
                {saving && <Loader2 className="animate-spin mr-2" size={16} />}
                {saving ? t('settings.ai.saving') : t('settings.ai.save')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
