import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies /api to the backend on :8000, so development needs no CORS and no
// environment variable.
//
// `vite preview` deliberately has no proxy here. It serves the production bundle, and a
// production bundle is built with VITE_API_URL pointing at a real API origin — `make preview`
// sets it to http://localhost:8000. Proxying preview as well would make it behave unlike the
// artefact that actually ships, and its proxy rejects some non-GET requests besides.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        rewrite: (path: string) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: { port: 5173 },
});
