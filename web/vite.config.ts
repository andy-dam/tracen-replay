import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// The build writes straight into the Go package that embeds it, so one
// `npm run build` followed by `go build` produces a single binary.
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: "../internal/webassets/dist",
    emptyOutDir: true,
  },
  server: {
    // The dev server proxies the API to a running service; TRACEN_API picks
    // a different instance (for example one started on another port).
    proxy: {
      "/api": process.env.TRACEN_API ?? "http://127.0.0.1:8765",
      "/healthz": process.env.TRACEN_API ?? "http://127.0.0.1:8765",
      "/readyz": process.env.TRACEN_API ?? "http://127.0.0.1:8765",
    },
  },
});
