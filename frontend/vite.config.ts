import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Ports par défaut inchangés (5173/8000, cf. README/start.sh) — surchageables via variables
// d'environnement pour faire tourner plusieurs instances en parallèle (ex. tester une branche
// dans un worktree sans interrompre un serveur de dev déjà actif sur les ports par défaut).
const frontendPort = Number(process.env.VITE_FRONTEND_PORT ?? 5173)
const backendPort = Number(process.env.VITE_BACKEND_PORT ?? 8000)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: frontendPort,
    proxy: {
      '/api': `http://localhost:${backendPort}`,
      '/ws': {
        target: `ws://localhost:${backendPort}`,
        ws: true,
      },
    },
  },
})
