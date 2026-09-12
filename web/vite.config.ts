import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // 5173 is one of the origins CORS_ORIGINS lists by default; changing it here
  // means changing it there too.
  server: { port: 5173, strictPort: true },
});
