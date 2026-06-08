import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { UserPlus } from 'lucide-react'
import { authApi } from '../api/auth'
import { useUserStore } from '../stores/useUserStore'

export default function Register() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const login = useUserStore((s) => s.login)
  const [form, setForm] = useState({ username: '', email: '', phone: '', password: '', confirmPassword: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleChange = (key: string, value: string) => setForm((f) => ({ ...f, [key]: value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.username || !form.password || !form.email) { setError('请填写必填字段'); return }
    if (form.password !== form.confirmPassword) { setError('两次密码不一致'); return }
    setLoading(true)
    setError('')
    try {
      const res = await authApi.register({
        username: form.username,
        password: form.password,
        email: form.email,
        telephone: form.phone || undefined,
        confirm_password: form.confirmPassword,
      })
      login(res.token, res.user)
      navigate('/notes')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      if (detail && typeof detail === 'object') {
        const msg = Object.values(detail as Record<string, unknown>).flat().join('；')
        setError(msg || '注册失败，请重试')
      } else if (typeof detail === 'string') {
        setError(detail)
      } else {
        setError('注册失败，请重试')
      }
    } finally {
      setLoading(false)
    }
  }

  const fields = [
    { key: 'username', label: t('auth.username'), required: true },
    { key: 'email', label: t('auth.email'), type: 'email', required: true },
    { key: 'phone', label: t('auth.phone'), type: 'tel' },
    { key: 'password', label: t('auth.password'), type: 'password', required: true },
    { key: 'confirmPassword', label: t('auth.confirmPassword'), type: 'password', required: true },
  ]

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h1 className="font-heading text-2xl font-semibold text-[var(--color-text)]">{t('auth.register')}</h1>
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{t('auth.register')}</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        {error && (
          <div className="px-4 py-3 rounded-md text-sm bg-[var(--color-danger-bg)] text-[var(--color-danger)]">{error}</div>
        )}

        {fields.map(({ key, label, type = 'text', required }) => (
          <div key={key} className="space-y-2">
            <label className="block text-sm font-medium text-[var(--color-text)]">
              {label}{required && <span className="text-[var(--color-danger)] ml-0.5">*</span>}
            </label>
            <input
              type={type}
              value={form[key as keyof typeof form]}
              onChange={(e) => handleChange(key, e.target.value)}
              className="w-full px-4 py-2.5 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent transition-colors"
              placeholder={label}
            />
          </div>
        ))}

        <button
          type="submit"
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-md bg-[var(--color-accent)] text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading ? (
            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            <UserPlus size={16} />
          )}
          {t('auth.register')}
        </button>
      </form>

      <p className="text-center text-sm text-[var(--color-text-secondary)]">
        {t('auth.hasAccount')}{' '}
        <Link to="/login" className="text-[var(--color-accent)] hover:underline">{t('auth.login')}</Link>
      </p>
    </div>
  )
}
