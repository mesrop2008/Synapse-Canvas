import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Listed in the default CORS_ORIGINS; change both together.
  server: { port: 5173, strictPort: true },
});
