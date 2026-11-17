import apiClient from "@/lib/api-client"

/** 升级任务步骤（执行器逐条落盘，面板轮询展示） */
export interface UpgradeStep {
  t: number // unix 秒
  msg: string
}

/** GET /admin-api/upgrade/check */
export interface UpgradeCheckData {
  current: string // 镜像构建期版本，源码运行为 dev
  latest: string
  has_update: boolean
  versions: string[] // 可用版本，新 → 旧
  image: string // 当前 PAY_IMAGE
  registry_error: string // 查询失败原因（空 = 正常）
  upgrader: boolean // 一键升级执行器是否存活（心跳 60s 内）
  status: UpgradeStatusData
}

/** GET /admin-api/upgrade/status（idle/queued/running/success/failed） */
export interface UpgradeStatusData {
  state: "idle" | "queued" | "running" | "success" | "failed"
  target?: string
  image?: string
  steps?: UpgradeStep[]
  requested_at?: number
  started_at?: number
  finished_at?: number
}

export const upgradeApi = {
  // 检查更新：当前版本 vs 镜像仓库最新版（含执行器存活与进行中任务）
  check: () => apiClient.get("/upgrade/check") as Promise<UpgradeCheckData>,

  // 触发一键升级（version 空 = 最新；需 upgrader 存活）
  run: (version?: string) =>
    apiClient.post("/upgrade/run", { version: version || "" }) as Promise<{
      id: string
      version: string
      image: string
    }>,

  // 升级任务状态（面板轮询）
  status: () => apiClient.get("/upgrade/status") as Promise<
    UpgradeStatusData & { current: string; upgrader: boolean }
  >,
}
