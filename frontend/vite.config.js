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
            // pdf.js + react-pdf: lazy PDFViewer chunk only (return undefined = no forced group)
            if (id.includes('react-pdf') || id.includes('pdfjs-dist')) return;
            // Syntax highlighter: lazy CodeBlock chunk only
            if (id.includes('react-syntax-highlighter') || id.includes('refractor') || id.includes('prismjs')) return;
            // Core React runtime — tiny, changes rarely
            if (id.includes('react-dom') || id.includes('react/')) return 'vendor-react';
            // HTTP client
            if (id.includes('axios')) return 'vendor-axios';
            // Lightweight UI utilities
            if (id.includes('react-hot-toast') || id.includes('react-markdown') || id.includes('remark') || id.includes('rehype')) return 'vendor-ui';
            // Data-viz — only loaded when graph panel is open
            if (id.includes('react-force-graph') || id.includes('d3-') || id.includes('three')) return 'vendor-graph';
            // Server-state / query
            if (id.includes('@tanstack')) return 'vendor-query';
            // Catch-all stable vendor code
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
