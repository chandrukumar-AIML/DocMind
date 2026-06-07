import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  esbuild: { jsx: 'automatic' },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            // pdf.js is only used by the lazy-loaded PDFViewer. Returning undefined
            // lets Rollup keep it inside that async chunk so it is fetched on demand
            // instead of being force-grouped into an eagerly-preloaded vendor chunk.
            if (id.includes('react-pdf') || id.includes('pdfjs-dist')) return;
            if (id.includes('react-dom') || id.includes('react/')) return 'vendor-react';
            if (id.includes('axios')) return 'vendor-axios';
            if (id.includes('react-hot-toast') || id.includes('react-markdown')) return 'vendor-ui';
            if (id.includes('react-force-graph') || id.includes('d3-') || id.includes('three')) return 'vendor-graph';
            return 'vendor';
          }
        },
      },
    },
  },
  server: {
    port: 5175,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    css: false,
  },
})
