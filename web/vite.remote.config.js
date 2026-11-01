import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
export default defineConfig({plugins:[vue()],base:'./',build:{outDir:'dist',emptyOutDir:false,rollupOptions:{
  input:fileURLToPath(new URL('./remote-camera.html',import.meta.url)),output:{inlineDynamicImports:true,
    entryFileNames:'assets/remote-camera/[name]-[hash].js',chunkFileNames:'assets/remote-camera/[name]-[hash].js',assetFileNames:'assets/remote-camera/[name]-[hash][extname]'}
}}})
