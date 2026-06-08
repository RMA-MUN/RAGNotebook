import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { UserInfo } from '../types/api'

interface UserState {
  userInfo: UserInfo | null
  token: string
  isLogin: boolean
  userBio: string
  login: (token: string, user: UserInfo) => void
  logout: () => void
  setUserInfo: (info: UserInfo) => void
  setToken: (token: string) => void
  setUserBio: (bio: string) => void
}

export const useUserStore = create<UserState>()(
  persist(
    (set) => ({
      userInfo: null,
      token: '',
      isLogin: false,
      userBio: '',
      login: (token, user) => {
        localStorage.setItem('jwt_token', token)
        set({ token, userInfo: user, isLogin: true })
      },
      logout: () => {
        localStorage.removeItem('jwt_token')
        set({ token: '', userInfo: null, isLogin: false, userBio: '' })
      },
      setUserInfo: (info) => set({ userInfo: info }),
      setToken: (token) => {
        localStorage.setItem('jwt_token', token)
        set({ token })
      },
      setUserBio: (bio) => set({ userBio: bio }),
    }),
    { name: 'user-store' }
  )
)
