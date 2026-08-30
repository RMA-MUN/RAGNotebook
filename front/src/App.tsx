import { useEffect } from 'react'
import { useNavigate, useRoutes } from 'react-router-dom'
import routes from './router'
import { useThemeStore } from './stores/useThemeStore'
import { installWikiLinkClickHandler, setWikiNavigator } from './utils/wikiNav'

function App() {
  const theme = useThemeStore((s) => s.theme)
  const routing = useRoutes(routes)
  const navigate = useNavigate()

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  useEffect(() => {
    setWikiNavigator(navigate)
    installWikiLinkClickHandler()
  }, [navigate])

  return <>{routing}</>
}

export default App
