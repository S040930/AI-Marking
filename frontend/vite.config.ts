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

// 生产预览(``start.prod.sh``)使用的严格安全响应头。
// 说明:
// - style-src 允许 'unsafe-inline':sonner/部分组件运行时注入 <style>
// - frame-src 'self':PdfViewer 以同源 iframe 内嵌 /api/*/pdf
// - 不携带 'unsafe-inline' 的 script-src:禁止一切内联脚本
const previewHeaders = {
  'Content-Security-Policy': [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join('; '),
  'X-Content-Type-Options': 'nosniff',
  // 禁止页面作为 Referer 出站,避免 URL 中的 access_token 泄露
  'Referrer-Policy': 'no-referrer',
}

// dev 模式放宽项(仅本机 127.0.0.1 可达):
// - script-src 允许 'unsafe-inline':@vitejs/plugin-react 的 react-refresh 内联 preamble
// - connect-src 允许 ws:HMR websocket 通道
const devHeaders = {
  'Content-Security-Policy': [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' ws://localhost:5173 ws://127.0.0.1:5173",
    "frame-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join('; '),
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
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
    // 仅本机监听,避免 dev 模式经 /api 代理把后端暴露到局域网
    host: '127.0.0.1',
    port: 5173,
    // 端口被其他项目占用时直接失败，避免 Vite 自动切换到 5174，
    // 启动脚本仍显示 5173 导致浏览器连到错误的应用。
    strictPort: true,
    proxy: apiProxy,
    headers: devHeaders,
  },
  // ``start.prod.sh`` 使用 ``vite preview``。preview 不会自动复用
  // ``server.proxy``，否则生产预览下 /api/* 会被静态服务器直接返回 404。
  preview: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
    headers: previewHeaders,
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
