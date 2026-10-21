import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard calls /api/*, which Vite forwards to the FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.APPLYPILOT_API ?? "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
