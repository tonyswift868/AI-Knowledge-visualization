// Build: node D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs
// Dependencies live outside the product tree (ENV-01), pinned by this package-lock.json.
import { fileURLToPath, pathToFileURL } from "node:url"
import path from "node:path"

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, "../../..")
const deps = process.env.MENTORFLOW_WEB_DEPS || path.join(repo, ".local", "web-deps")
const load = (p) => import(pathToFileURL(path.join(deps, "node_modules", p)).href)
const { default: react } = await load("@vitejs/plugin-react/dist/index.js")

// Bare imports from our own sources resolve against the pinned dependency folder.
const depsResolver = {
  name: "mentorflow-deps",
  enforce: "pre",
  async resolveId(source, importer, options) {
    if (!importer || source.startsWith(".") || source.startsWith("/") || source.startsWith("\0") || /^[A-Za-z]:/.test(source)) return null
    if (importer.includes("node_modules")) return null
    const resolved = await this.resolve(source, path.join(deps, "package.json"), { ...options, skipSelf: true })
    return resolved
  },
}

export default {
  root: here,
  base: "./",
  plugins: [depsResolver, react()],
  build: { outDir: path.join(here, "dist"), emptyOutDir: true, assetsInlineLimit: 8192, chunkSizeWarningLimit: 1500 },
}
