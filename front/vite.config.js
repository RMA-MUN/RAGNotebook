import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    host: true, // 允许局域网访问
    proxy: {
      // AI相关接口代理到8000端口
      '/api/agent': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true
      },
      '/api/rag': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      '/api/session': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      '/knowledge/': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      '/chat': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      // 用户相关接口代理到8000端口（FastAPI 统一处理）
      '/user': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      '/file': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})