import { Monitor, Moon, Sun } from 'lucide-react'
import { useLayoutEffect, useState } from 'react'

type ThemeMode = 'light' | 'dark' | 'automatic'

function initialMode(): ThemeMode {
  const stored = window.localStorage.getItem('workflow-theme-mode')?.toLowerCase()
  if (stored === 'light' || stored === 'dark' || stored === 'automatic') return stored
  const deskTheme = window.frappe?.boot?.desk_theme?.toLowerCase()
  if (deskTheme === 'dark' || deskTheme === 'automatic') return deskTheme
  return 'light'
}

function applyTheme(mode: ThemeMode) {
  const dark = mode === 'dark' || (mode === 'automatic' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.themeMode = mode
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

export function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(initialMode)

  useLayoutEffect(() => {
    applyTheme(mode)
    window.localStorage.setItem('workflow-theme-mode', mode)
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const sync = () => applyTheme(mode)
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [mode])

  const next = mode === 'light' ? 'dark' : mode === 'dark' ? 'automatic' : 'light'
  const Icon = mode === 'light' ? Sun : mode === 'dark' ? Moon : Monitor
  return (
    <button className="icon-button" type="button" title={`Theme: ${mode}. Switch to ${next}.`} aria-label={`Theme: ${mode}`} onClick={() => setMode(next)}>
      <Icon size={16} />
    </button>
  )
}
