import { defineConfig } from "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/vite/dist/node/index.js"
import react from "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/@vitejs/plugin-react/dist/index.js"

export default defineConfig({
  root: ".",
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      react: "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/react",
      "react-dom": "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/react-dom",
      "lucide-react": "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/lucide-react",
      "pixi.js": "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/pixi.js",
      "untitled-pixi-live2d-engine/cubism": "E:/aiPlay/AI-Knowledge visualization/frontend/node_modules/untitled-pixi-live2d-engine/dist/cubism.es.js",
    },
  },
  build: { outDir: "dist", emptyOutDir: true, rollupOptions: { input: "index.html" } },
})
