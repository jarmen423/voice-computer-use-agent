/**
 * Purpose: Vite configuration for the Cybernetic Autopilot landing page.
 * Dependencies: vite, @vitejs/plugin-react
 * Role: Packages and bundles the landing page application.
 */

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true
  }
});
