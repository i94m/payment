interface GlobalConfig {
  module: "admin" | "merchant" | "agent"
  api: string
}

declare global {
  var GLOBAL: GlobalConfig
}

const cfg = typeof window !== "undefined" && window.GLOBAL
  ? window.GLOBAL
  : { module: "admin" as const, api: "/admin-api" }

export const APP_MODULE = cfg.module
export const BASE_URL = cfg.api
export const STORAGE_PREFIX = cfg.module + ":"
