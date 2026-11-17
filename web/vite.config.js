import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the Python package so `python -m aiauto server` serves it.
// `npm run dev` proxies API / websocket calls to a hub on :8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../aiauto/server/static/app", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/artifacts": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
