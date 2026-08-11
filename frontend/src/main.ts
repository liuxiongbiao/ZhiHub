/**
 * frontend/src/main.ts
 *
 * 应用入口，初始化 Vue、Pinia、Router、Element Plus，接线路由守卫与 401 回跳。
 *
 * ── 启动顺序 ──
 *   1. createApp(App)                     → 创建 Vue 应用实例
 *   2. app.use(createPinia())             → 安装 Pinia 状态管理
 *   3. app.use(router)                    → 安装 Vue Router
 *   4. app.use(ElementPlus)               → 全局注册 Element Plus 组件
 *   5. registerGuard(router)              → 注册 beforeEach 路由守卫（permission.ts）
 *   6. wireUnauthorized(router)           → 接线 401 回跳逻辑（permission.ts → request.ts）
 *   7. app.mount('#app')                  → 挂载到 DOM
 *
 * ── 关键：registerGuard/wireUnauthorized 放在 app.use(router) 之后、mount 之前 ──
 *   守卫内部通过惰性 import('@/store/modules/user') 取 store，
 *   pinia 已在 step 2 安装，因此守卫运行时 store 可用。
 *
 * ── 守卫不放在 router/index.ts 内的原因 ──
 *   便于测试：测试可新建独立 router 实例 + 手动调 registerGuard/wireUnauthorized，
 *   不依赖全局单例 router 的状态。
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/global.scss'
import router from './router'
import App from './App.vue'
import { registerGuard, wireUnauthorized } from './permission'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)

registerGuard(router)
wireUnauthorized(router)

app.mount('#app')
