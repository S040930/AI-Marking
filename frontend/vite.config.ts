import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { compression } from 'vite-plugin-compression2'

// https://vite.dev/config/
export default defineConfig({
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
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
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
