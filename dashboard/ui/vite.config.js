import { defineConfig } from 'vite';
import { resolve } from 'path';
import tailwindcss from '@tailwindcss/vite';
import { createHtmlPlugin } from 'vite-plugin-html';

const gatewayPort = process.env.GATEWAY_PORT || 18789;

export default defineConfig({
  root: resolve(__dirname, 'src'),
  base: '/ui/',
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, 'src', 'dashboard.html'),
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name].[ext]',
      },
    },
  },
  plugins: [
    tailwindcss(),
    createHtmlPlugin({
      minify: true,
      inject: {
        ejsOptions: { filename: resolve(__dirname, 'src', 'dashboard.html') },
      },
    }),
  ],
  server: {
    proxy: {
      '/api': `http://localhost:${gatewayPort}`,
      '/ws': { target: `ws://localhost:${gatewayPort}`, ws: true },
      '/metrics': `http://localhost:${gatewayPort}`,
    },
  },
});
