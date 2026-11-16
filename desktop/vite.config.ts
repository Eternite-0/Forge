import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { watch: { ignored: ["**/src-tauri/target/**"] } },
  build: {
    rollupOptions: {
      output: { manualChunks: { markdown: ["react-markdown", "remark-gfm"] } },
    },
  },
});
