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
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/healthz": "http://127.0.0.1:8765",
      "/readyz": "http://127.0.0.1:8765",
    },
  },
});
