import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const BACKEND_TARGET = process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8000'
const USER_TARGET = process.env.VITE_USER_TARGET || 'http://127.0.0.1:8001'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/chat/agent/': { target: BACKEND_TARGET, changeOrigin: true, ws: true },
      '/chat/rag/': { target: BACKEND_TARGET, changeOrigin: true },
      '/chat/session/': { target: BACKEND_TARGET, changeOrigin: true },
      '/chat/sessions': { target: BACKEND_TARGET, changeOrigin: true },
      '/chat/reorder': { target: BACKEND_TARGET, changeOrigin: true },
      '/knowledge/': { target: BACKEND_TARGET, changeOrigin: true },
      '/note/': { target: BACKEND_TARGET, changeOrigin: true },
      '/review/': { target: BACKEND_TARGET, changeOrigin: true },
      '/health': { target: BACKEND_TARGET, changeOrigin: true },
      '/user': { target: USER_TARGET, changeOrigin: true },
      '/file': { target: USER_TARGET, changeOrigin: true },
    },
  },
})
