import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  publicDir: "public",
  server: {
    port: 5173,
    open: false,
  },
  optimizeDeps: {
    exclude: ["seekdb"],
  },
  build: {
    target: "esnext",
  },
});
