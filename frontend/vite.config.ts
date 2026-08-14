import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { compression } from 'vite-plugin-compression2'

// https://vite.dev/config/
const apiProxy = {
  '/api': {
    // 后端仅监听 IPv4；使用 127.0.0.1 避免部分系统将 localhost 优先
    // 解析为 ::1，导致浏览器虽能加载 Vite 页面但所有 API 请求被拒绝。
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
  },
}

export default defineConfig({
  appType: 'spa',
  plugins: [
    react(),
    tailwindcss(),
    // 预压缩:同时生成 .gz 与 .br,服务器可直接返回预压缩资源
    compression({
      algorithms: ['gzip', 'brotliCompress'],
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5173,
    // 端口被其他项目占用时直接失败，避免 Vite 自动切换到 5174，
    // 启动脚本仍显示 5173 导致浏览器连到错误的应用。
    strictPort: true,
    proxy: apiProxy,
  },
  // ``start.prod.sh`` 使用 ``vite preview``。preview 不会自动复用
  // ``server.proxy``，否则生产预览下 /api/* 会被静态服务器直接返回 404。
  preview: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  build: {
    rollupOptions: {
      output: {
        // 拆分 vendor chunk,降低单 chunk 体积,利于浏览器缓存
        manualChunks(id: string) {
          if (!id.includes('node_modules/')) return undefined
          if (
            id.includes('react/') ||
            id.includes('react-dom/') ||
            id.includes('react-router-dom')
          ) {
            return 'react-vendor'
          }
          if (id.includes('@tanstack/react-query')) {
            return 'query-vendor'
          }
          if (
            id.includes('react-hook-form') ||
            id.includes('@hookform/resolvers') ||
            id.includes('/zod/')
          ) {
            return 'form-vendor'
          }
          if (id.includes('radix-ui')) {
            return 'radix-vendor'
          }
          return undefined
        },
      },
    },
    chunkSizeWarningLimit: 1000,
  },
})
