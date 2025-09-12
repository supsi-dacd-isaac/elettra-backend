import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: ['isaac-elettra.dacd.supsi.ch', 'localhost', '127.0.0.1', '10.9.0.5'],
    proxy: {
      '/auth': 'http://localhost:8002',
      '/api': 'http://localhost:8002',
    },
  },
})
