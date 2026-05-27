import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('react-dom') || id.includes('react/')) return 'vendor-react';
            if (id.includes('axios')) return 'vendor-axios';
            if (id.includes('react-hot-toast') || id.includes('react-markdown')) return 'vendor-ui';
            if (id.includes('react-pdf') || id.includes('pdfjs-dist')) return 'vendor-pdf';
            if (id.includes('react-force-graph') || id.includes('d3-') || id.includes('three')) return 'vendor-graph';
            return 'vendor';
          }
        },
      },
    },
  },
  server: {
    port: 5173,
  },
})
