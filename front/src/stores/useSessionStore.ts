import { create } from 'zustand'
import type { ChatSession } from '../types/api'

interface SessionState {
  sessions: ChatSession[]
  currentSession: ChatSession | null
  loading: boolean
  setSessions: (sessions: ChatSession[]) => void
  setCurrentSession: (session: ChatSession | null) => void
  addSession: (session: ChatSession) => void
  removeSession: (id: string) => void
  setLoading: (loading: boolean) => void
  clearSessions: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  currentSession: null,
  loading: false,
  setSessions: (sessions) => set({ sessions }),
  setCurrentSession: (session) => set({ currentSession: session }),
  addSession: (session) => set((s) => ({ sessions: [session, ...s.sessions] })),
  removeSession: (id) =>
    set((s) => ({
      sessions: s.sessions.filter((ss) => ss.id !== id),
      currentSession: s.currentSession?.id === id ? null : s.currentSession,
    })),
  setLoading: (loading) => set({ loading }),
  clearSessions: () => set({ sessions: [], currentSession: null }),
}))
