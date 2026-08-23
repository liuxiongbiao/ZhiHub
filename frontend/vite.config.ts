// frontend/vite.config.ts
// 职责: Vite构建配置，含API代理、路径别名

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
  build: {
    rollupOptions: {
      output: {
        // -5: 拆分框架/UI/HTTP 库，降低首屏主包体积
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          element: ['element-plus', '@element-plus/icons-vue'],
          http: ['axios'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.spec.ts'],
    setupFiles: ['tests/setup.ts'],
    globals: false,
    css: false,
    restoreMocks: true,
    // 组件测试在并行 jsdom 环境下偶发超时（机器负载相关），放宽默认超时
    testTimeout: 20000,
  },
})
