import path from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV !== "production";
const here = path.dirname(fileURLToPath(import.meta.url));
// Where the Python API (server.py) runs. `npm run dev` starts it for you if it isn't running yet.
const API_URL = (process.env.API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig = {
  // This folder is the app root (there is another package.json one level up; ignore it).
  turbopack: { root: here },
  outputFileTracingRoot: here,
  agentRules: false, // don't write an AGENTS.md into this folder on `next dev`
  // `npm run build` writes a static site to ./out, which server.py serves at http://127.0.0.1:8000.
  output: isDev ? undefined : "export",
  // `npm run dev` (http://localhost:3000): forward API calls to server.py.
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
    },
  }),
};

export default nextConfig;
