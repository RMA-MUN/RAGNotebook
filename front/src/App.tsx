import { useEffect } from 'react'
import { useRoutes } from 'react-router-dom'
import routes from './router'
import { useThemeStore } from './stores/useThemeStore'

function App() {
  const theme = useThemeStore((s) => s.theme)
  const routing = useRoutes(routes)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return <>{routing}</>
}

export default App
