import { StrictMode } from 'react'
import '@fontsource-variable/plus-jakarta-sans'
import { createRoot } from 'react-dom/client'
import './i18n'
import './theme'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
