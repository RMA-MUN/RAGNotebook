import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Camera, Lock, Save, X, Eye, EyeOff } from 'lucide-react'
import * as Dialog from '@radix-ui/react-dialog'
import { authApi } from '../api/auth'
import { useUserStore } from '../stores/useUserStore'
import type { UserInfo } from '../types/api'

export default function Profile() {
  const { t } = useTranslation()
  const { userInfo, setUserInfo, token } = useUserStore()
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ username: '', email: '', phone: '', gender: '', bio: '' })
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const [pwdOpen, setPwdOpen] = useState(false)
  const [pwdForm, setPwdForm] = useState({ oldPassword: '', newPassword: '', confirmPassword: '' })
  const [showPwd, setShowPwd] = useState({ old: false, new: false, confirm: false })
  const [pwdLoading, setPwdLoading] = useState(false)
  const [pwdError, setPwdError] = useState('')

  useEffect(() => {
    if (token) {
      authApi.getProfile().then((res) => {
        const data = (res.data as UserInfo | undefined)
        if (data) {
          const info: UserInfo = {
            username: data.username as string || '',
            email: data.email as string || '',
            phone: (data as unknown as { telephone?: string }).telephone || '',
            gender: data.gender as string || '',
            bio: data.bio as string || '',
            id: data.id as string,
            avatar: data.avatar as string,
          }
          setUserInfo(info)
        }
      }).catch(() => {})
    }
  }, [token])

  useEffect(() => {
    if (userInfo) {
      setForm({
        username: (userInfo.username as string) || '',
        email: (userInfo.email as string) || '',
        phone: (userInfo.phone as string) || '',
        gender: (userInfo.gender as string) || '',
        bio: (userInfo.bio as string) || '',
      })
    }
  }, [userInfo])

  const handleSave = async () => {
    setLoading(true)
    try {
      const payload = {
        username: form.username || undefined,
        telephone: form.phone || undefined,
        gender: form.gender ? Number(form.gender) : undefined,
        bio: form.bio || undefined,
      }
      const res = await authApi.updateProfile(payload)
      const newToken = (res as { token?: string }).token
      if (newToken) {
        useUserStore.getState().setToken(newToken)
      }
      const userField = (res as { user?: Record<string, unknown> }).user
      if (userField) {
        const info: UserInfo = {
          username: (userField.username as string) || form.username,
          email: (userField.email as string) || form.email,
          phone: (userField.telephone as string) || form.phone,
          gender: String(userField.gender ?? form.gender),
          bio: (userField.bio as string) || form.bio,
        }
        setUserInfo(info as UserInfo)
        setMessage(t('profile.save') + ' OK')
      }
      setEditing(false)
    } catch {
      setMessage('Error')
    } finally {
      setLoading(false)
      setTimeout(() => setMessage(''), 2000)
    }
  }

  const handlePasswordChange = async () => {
    const { oldPassword, newPassword, confirmPassword } = pwdForm
    if (!oldPassword || !newPassword || !confirmPassword) {
      setPwdError(t('common.fillAllFields'))
      return
    }
    if (newPassword.length < 6) {
      setPwdError(t('auth.passwordLength'))
      return
    }
    if (newPassword === oldPassword) {
      setPwdError(t('profile.samePassword'))
      return
    }
    if (newPassword !== confirmPassword) {
      setPwdError(t('profile.passwordMismatch'))
      return
    }
    setPwdLoading(true)
    setPwdError('')
    try {
      const res = await authApi.updatePassword(oldPassword, newPassword)
      if (res.token) {
        useUserStore.getState().setToken(res.token)
      }
      setPwdOpen(false)
      setPwdForm({ oldPassword: '', newPassword: '', confirmPassword: '' })
      setMessage(t('profile.passwordChanged'))
      setTimeout(() => setMessage(''), 2000)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setPwdError(detail || t('profile.passwordError'))
    } finally {
      setPwdLoading(false)
    }
  }

  const fields = [
    { key: 'username', label: t('profile.username') },
    { key: 'email', label: t('profile.email'), type: 'email' },
    { key: 'phone', label: t('profile.phone'), type: 'tel' },
  ]

  return (
    <div className="max-w-2xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-heading text-xl font-semibold text-[var(--color-text)]">{t('profile.title')}</h1>
        {!editing ? (
          <button onClick={() => setEditing(true)} className="px-4 py-2 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors">
            {t('profile.edit')}
          </button>
        ) : (
          <div className="flex gap-2">
            <button onClick={() => setEditing(false)} className="px-4 py-2 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors">
              <X size={14} className="inline mr-1" />{t('profile.cancel')}
            </button>
            <button onClick={handleSave} disabled={loading} className="px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white hover:bg-blue-700 disabled:opacity-50 transition-colors">
              <Save size={14} className="inline mr-1" />{t('profile.save')}
            </button>
          </div>
        )}
      </div>

      {message && (
        <div className="mb-4 px-4 py-2 rounded-md text-sm bg-[var(--color-success-bg)] text-[var(--color-success)]">{message}</div>
      )}

      <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] divide-y divide-[var(--color-divider)]">
        <div className="flex items-center gap-4 p-6">
          <div className="relative w-16 h-16 rounded-full bg-[var(--color-accent-bg)] flex items-center justify-center text-[var(--color-accent)] text-xl font-medium">
            {userInfo?.username ? (userInfo.username as string)[0].toUpperCase() : '?'}
            {editing && (
              <div className="absolute inset-0 rounded-full bg-black/30 flex items-center justify-center cursor-pointer">
                <Camera size={18} className="text-white" />
              </div>
            )}
          </div>
          <div>
            <p className="text-sm font-medium text-[var(--color-text)]">{userInfo?.username as string}</p>
            <p className="text-xs text-[var(--color-text-tertiary)]">{userInfo?.email as string}</p>
          </div>
        </div>

        {fields.map(({ key, label, type = 'text' }) => (
          <div key={key} className="flex items-center justify-between px-6 py-4">
            <span className="text-sm text-[var(--color-text-secondary)]">{label}</span>
            {editing ? (
              <input
                type={type}
                value={form[key as keyof typeof form]}
                onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                className="w-48 px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
              />
            ) : (
              <span className="text-sm text-[var(--color-text)]">{form[key as keyof typeof form] || '-'}</span>
            )}
          </div>
        ))}

        <div className="px-6 py-4">
          <div className="flex items-start justify-between">
            <span className="text-sm text-[var(--color-text-secondary)]">{t('profile.gender')}</span>
            {editing ? (
              <div className="flex gap-3">
                {[1, 2].map((g) => (
                  <label key={g} className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="gender"
                      value={g}
                      checked={Number(form.gender) === g}
                      onChange={(e) => setForm((f) => ({ ...f, gender: e.target.value }))}
                      className="text-[var(--color-accent)]"
                    />
                    <span className="text-sm text-[var(--color-text)]">{t(`profile.${g === 1 ? 'male' : 'female'}`)}</span>
                  </label>
                ))}
              </div>
            ) : (
              <span className="text-sm text-[var(--color-text)]">{form.gender ? t(`profile.${Number(form.gender) === 1 ? 'male' : 'female'}`) : '-'}</span>
            )}
          </div>
        </div>

        <div className="px-6 py-4">
          <div className="flex items-start justify-between">
            <span className="text-sm text-[var(--color-text-secondary)]">{t('profile.bio')}</span>
            {editing ? (
              <textarea
                value={form.bio}
                onChange={(e) => setForm((f) => ({ ...f, bio: e.target.value }))}
                rows={3}
                className="w-48 px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] resize-none"
              />
            ) : (
              <span className="text-sm text-[var(--color-text)] max-w-48 text-right">{form.bio || '-'}</span>
            )}
          </div>
        </div>
      </div>

      <button
        onClick={() => { setPwdOpen(true); setPwdError('') }}
        className="mt-6 flex items-center gap-2 px-4 py-2 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors"
      >
        <Lock size={14} />
        {t('profile.changePassword')}
      </button>

      <Dialog.Root open={pwdOpen} onOpenChange={(open) => { setPwdOpen(open); if (!open) setPwdError('') }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/40" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-[var(--color-card)] rounded-lg shadow-xl p-6 w-[420px] max-w-[90vw]">
            <div className="flex items-center justify-between mb-5">
              <Dialog.Title className="text-base font-medium text-[var(--color-text)]">
                {t('profile.changePassword')}
              </Dialog.Title>
              <Dialog.Close className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text)]">
                <X size={16} />
              </Dialog.Close>
            </div>

            {pwdError && (
              <div className="mb-4 px-4 py-2 rounded-md text-sm bg-[var(--color-danger-bg)] text-[var(--color-danger)]">
                {pwdError}
              </div>
            )}

            <div className="space-y-4">
              {(['oldPassword', 'newPassword', 'confirmPassword'] as const).map((field) => (
                <div key={field} className="space-y-1.5">
                  <label className="block text-sm text-[var(--color-text-secondary)]">
                    {field === 'oldPassword' ? t('profile.oldPassword') : field === 'newPassword' ? t('profile.newPassword') : t('profile.confirmPassword')}
                  </label>
                  <div className="relative">
                    <input
                      type={showPwd[field === 'oldPassword' ? 'old' : field === 'newPassword' ? 'new' : 'confirm'] ? 'text' : 'password'}
                      value={pwdForm[field]}
                      onChange={(e) => setPwdForm((f) => ({ ...f, [field]: e.target.value }))}
                      className="w-full px-4 py-2.5 pr-10 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-sm text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent transition-colors"
                      placeholder={field === 'oldPassword' ? t('profile.oldPassword') : field === 'newPassword' ? t('profile.newPassword') : t('profile.confirmPassword')}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPwd((s) => ({ ...s, [field === 'oldPassword' ? 'old' : field === 'newPassword' ? 'new' : 'confirm']: !showPwd[field === 'oldPassword' ? 'old' : field === 'newPassword' ? 'new' : 'confirm'] }))}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
                    >
                      {showPwd[field === 'oldPassword' ? 'old' : field === 'newPassword' ? 'new' : 'confirm'] ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <Dialog.Close className="px-4 py-2 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors">
                {t('profile.cancel')}
              </Dialog.Close>
              <button
                onClick={handlePasswordChange}
                disabled={pwdLoading}
                className="flex items-center gap-2 px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {pwdLoading ? (
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <Lock size={14} />
                )}
                {t('profile.changePassword')}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
