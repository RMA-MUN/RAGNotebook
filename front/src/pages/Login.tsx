import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff, LogIn } from 'lucide-react'
import { authApi } from '../api/auth'
import { useUserStore } from '../stores/useUserStore'
import { useLanguageStore } from '../stores/useLanguageStore'
import i18n from '../i18n'

export default function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const login = useUserStore((s) => s.login)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) { setError('请输入用户名和密码'); return }
    setLoading(true)
    setError('')
    try {
      const res = await authApi.login(username, password)
      login(res.token, res.user)
      i18n.changeLanguage(useLanguageStore.getState().lang)
      navigate('/notes')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      if (detail && typeof detail === 'object') {
        const msg = Object.values(detail as Record<string, unknown>).flat().join('；')
        setError(msg || '登录失败，请检查用户名和密码')
      } else if (typeof detail === 'string') {
        setError(detail)
      } else {
        setError('登录失败，请检查用户名和密码')
      }
    } finally {
      setLoading(false)
    }
  }

  const fillTestAccount = () => {
    setUsername('admin')
    setPassword('admin1234')
  }

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h1 className="font-heading text-2xl font-semibold text-[var(--color-text)]">
          {t('app.name')}
        </h1>
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{t('auth.login')}</p>
      </div>

      <form onSubmit={handleLogin} className="space-y-5">
        {error && (
          <div className="px-4 py-3 rounded-md text-sm bg-[var(--color-danger-bg)] text-[var(--color-danger)]">
            {error}
          </div>
        )}

        <div className="space-y-2">
          <label className="block text-sm font-medium text-[var(--color-text)]">{t('auth.username')}</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full px-4 py-2.5 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent transition-colors"
            placeholder={t('auth.username')}
          />
        </div>

        <div className="space-y-2">
          <label className="block text-sm font-medium text-[var(--color-text)]">{t('auth.password')}</label>
          <div className="relative">
            <input
              type={showPwd ? 'text' : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2.5 pr-10 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent transition-colors"
              placeholder={t('auth.password')}
            />
            <button type="button" onClick={() => setShowPwd(!showPwd)} className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
              {showPwd ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-md bg-[var(--color-accent)] text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading ? (
            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            <LogIn size={16} />
          )}
          {t('auth.login')}
        </button>
      </form>

      <div className="flex flex-col items-center gap-3">
        <button onClick={fillTestAccount} className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-accent)] transition-colors">
          {t('auth.testAccount')}
        </button>
        <p className="text-sm text-[var(--color-text-secondary)]">
          {t('auth.noAccount')}{' '}
          <Link to="/register" className="text-[var(--color-accent)] hover:underline">{t('auth.register')}</Link>
        </p>
      </div>
    </div>
  )
}
