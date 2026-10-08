import { defineConfig, type Plugin } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import path from "path"
import fs from "fs"

// 宣传页源文件位于产物目录 static/（vite root 之外），dev 下直接回源返回
const landingFile = path.resolve(__dirname, "../../static/index.html")

// 开发期入口路径对齐生产（app/core/frontend.py）：
// /admin[/] → admin.html、/agent[/] → agent.html、/landing[/] → static/index.html。
// vite 的 html fallback 只对无后缀且不以 / 结尾的路径尝试 *.html，
// 带尾部斜杠的 /admin/ 会兜底到根 index.html（商户端），拿到错误入口。
function entryPathPlugin(): Plugin {
  return {
    name: "entry-path-rewrite",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = (req.url ?? "").split("?")[0]
        if (pathname === "/admin" || pathname === "/admin/") req.url = "/admin.html"
        else if (pathname === "/agent" || pathname === "/agent/") req.url = "/agent.html"
        else if (pathname === "/landing" || pathname === "/landing/") {
          if (fs.existsSync(landingFile)) {
            res.setHeader("Content-Type", "text/html; charset=utf-8")
            fs.createReadStream(landingFile).pipe(res)
          } else {
            res.statusCode = 404
            res.end("landing 未找到：static/index.html")
          }
          return
        }
        next()
      })
    },
  }
}

export default defineConfig({
  // 相对路径：后端把入口挂在 /（商户端）与 /admin（总后台）下，绝对 /assets 会 404
  base: "./",
  plugins: [entryPathPlugin(), react(), tailwindcss()],
  build: {
    outDir: path.resolve(__dirname, "../../static/frontend"),
    emptyOutDir: true,
    manifest: "manifest.json",
    rollupOptions: {
      input: {
        index: path.resolve(__dirname, "index.html"), // 商户端，挂载在 /
        admin: path.resolve(__dirname, "admin.html"), // 总后台，挂载在 /admin
        agent: path.resolve(__dirname, "agent.html"), // 代理面板，挂载在 /agent
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/admin-api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/merchant-api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/agent-api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/storage": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/upload": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
})
