import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const serverPort = Number(env.VITE_PORT) || 5173

  return {
    base: './',
    build: {
      target: 'esnext' // allows top-level await
    },
    plugins: [
      react(),
      federation({
        name: 'host-app',
        remotes: {},
        shared: {
          react: {
            singleton: true,
            requiredVersion: '18.3.1',
            eager: true
          },
          'react-dom': {
            singleton: true,
            requiredVersion: '18.3.1',
            eager: true
          }
        }
      })
    ],

    server: {
      host: '0.0.0.0',
      port: serverPort,
      force: true, // Force dependency pre-bundling
      hmr: {
        overlay: true
      },
      proxy: {
        '/api': {
          target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8005',
          changeOrigin: true,
          secure: false,
          configure: (proxy, options) => {
            // Log proxy requests for debugging
            proxy.on('proxyReq', (proxyReq, req, res) => {
              console.log(`[PROXY] ${req.method} ${req.url} -> ${options.target}${req.url}`)
            })
          }
        }
      }
    },
    optimizeDeps: {
      force: true // Force re-optimization of dependencies
    }
  }
})
