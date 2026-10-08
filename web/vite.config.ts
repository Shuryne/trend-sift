import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Production serves static files under /assets; hash icons with the other assets.
  build: { assetsInlineLimit: 0 },
  server: {
    port: 8111,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  preview: { port: 8111, strictPort: true },
});
