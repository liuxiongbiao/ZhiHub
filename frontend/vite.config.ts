// frontend/vite.config.ts
// 职责: Vite构建配置，含API代理、路径别名
// 分工: 🟢 弱模型可独立完成
// 对应日程: Day 1

import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') }
  },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true }
    }
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.spec.ts'],
    setupFiles: ['tests/setup.ts'],
    globals: false,
    css: false,
    restoreMocks: true,
  },
})
