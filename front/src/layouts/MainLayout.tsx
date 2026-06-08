import { useState } from 'react'
import { Outlet, Navigate } from 'react-router-dom'
import Sidebar from '../components/layout/Sidebar'
import { useUserStore } from '../stores/useUserStore'

export default function MainLayout() {
  const isLogin = useUserStore((s) => s.isLogin)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  if (!isLogin) {
    return <Navigate to="/login" replace />
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-bg)]">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
      />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
