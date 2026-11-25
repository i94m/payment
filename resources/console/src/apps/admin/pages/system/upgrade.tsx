"use client"

import * as React from "react"
import { upgradeApi, accountApi } from "@/api"
import type { UpgradeCheckData, UpgradeStatusData } from "@/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { CopyButton } from "@/components/ui/copy-button"
import { useSiteStore } from "@/stores/site-store"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { IconRefresh, IconRocket, IconAlertCircle } from "@tabler/icons-react"

// 版本展示统一加 v 前缀（dev 等非数字开头原样展示）
const fmtVersion = (v?: string) => (!v ? "—" : /^\d/.test(v) ? `v${v}` : v)
const fmtTime = (t?: number) =>
  t ? new Date(t * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : ""

const STATE_TEXT: Record<UpgradeStatusData["state"], string> = {
  idle: "空闲",
  queued: "已提交，等待执行器",
  running: "升级中",
  success: "已完成",
  failed: "失败",
}

export function UpgradePage() {
  const [check, setCheck] = React.useState<UpgradeCheckData | null>(null)
  const [sysInfo, setSysInfo] = React.useState<{ build_date?: string; revision?: string } | null>(null)
  const [status, setStatus] = React.useState<UpgradeStatusData | null>(null)
  const [checking, setChecking] = React.useState(false)
  const [confirmVersion, setConfirmVersion] = React.useState<string | null>(null)
  const [submitting, setSubmitting] = React.useState(false)
  const fetchSite = useSiteStore((s) => s.fetchSite)

  const runCheck = React.useCallback(async () => {
    setChecking(true)
    try {
      const [ck, info] = await Promise.all([
        upgradeApi.check(),
        accountApi.systemInfo().catch(() => null),
      ])
      setCheck(ck)
      setStatus(ck.status)
      if (info) setSysInfo(info)
    } catch (err) {
      toast.error(getErrorMessage(err, "检查更新失败"))
    } finally {
      setChecking(false)
    }
  }, [])

  // 首次进入自动检查一次（微任务里启动，避免 effect 内同步 setState）；
  // 有进行中任务则由下方轮询 effect 接管
  React.useEffect(() => {
    Promise.resolve().then(runCheck)
  }, [runCheck])

  React.useEffect(() => {
    if (!status || (status.state !== "queued" && status.state !== "running")) return
    const timer = setInterval(async () => {
      try {
        const st = await upgradeApi.status()
        const prev = status.state
        setStatus(st)
        if (prev !== st.state && st.state === "success") {
          toast.success(`已升级到 ${fmtVersion(st.target)}`)
          fetchSite() // 侧边栏版本号随新版本刷新
          runCheck()
        } else if (prev !== st.state && st.state === "failed") {
          toast.error("升级失败，详见进度日志")
        }
      } catch {
        // 升级中容器逐台重启，偶发网络错误静默重试
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [status, fetchSite, runCheck])

  const submitUpgrade = async () => {
    if (!confirmVersion) return
    setSubmitting(true)
    try {
      const res = await upgradeApi.run(confirmVersion)
      setStatus({ state: "queued", target: res.version, steps: [] })
      toast.success(`升级请求已提交（${fmtVersion(res.version)}）`)
      setConfirmVersion(null)
    } catch (err) {
      toast.error(getErrorMessage(err, "提交失败"))
    } finally {
      setSubmitting(false)
    }
  }

  const running = status?.state === "queued" || status?.state === "running"

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <div className="px-4 lg:px-6 grid gap-4 lg:grid-cols-2">
          {/* 当前版本 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <IconRocket className="size-4" />
                {"当前版本"}
              </CardTitle>
              <CardDescription>
                {"版本号随镜像构建写入；检查更新会在线查询镜像仓库（ghcr.io）的可用版本"}
              </CardDescription>
              <CardAction>
                <Button size="sm" variant="outline" onClick={runCheck} disabled={checking}>
                  <IconRefresh className={checking ? "size-4 animate-spin" : "size-4"} />
                  {checking ? "检查中..." : "检查更新"}
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="space-y-3">
              {!check ? (
                <Skeleton className="h-16 w-full" />
              ) : (
                <>
                  <div className="flex items-center gap-3">
                    <span className="text-3xl font-semibold tracking-tight">
                      {fmtVersion(check.current)}
                    </span>
                    {check.has_update ? (
                      <Badge>可升级到 {fmtVersion(check.latest)}</Badge>
                    ) : (
                      check.latest && <Badge variant="secondary">已是最新</Badge>
                    )}
                  </div>
                  <div className="space-y-1 text-sm text-muted-foreground">
                    <p>{"构建提交：" + (sysInfo?.revision || "—")}</p>
                    <p>{"当前镜像：" + (check.image || "—")}</p>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* 可用版本 / 升级入口 */}
          <Card>
            <CardHeader>
              <CardTitle>{"可用版本"}</CardTitle>
              <CardDescription>
                {check?.upgrader
                  ? "选择版本一键升级：先迁移数据库，再逐台重启服务（多副本近似无感，单副本中断约 10-30 秒）"
                  : "在服务器执行命令升级；开启后台一键升级见 DEPLOY.md「版本升级」"}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {checking && !check ? (
                <Skeleton className="h-24 w-full" />
              ) : !check ? (
                <p className="text-sm text-muted-foreground">{"点击「检查更新」获取版本列表"}</p>
              ) : check.registry_error ? (
                <Alert variant="destructive">
                  <IconAlertCircle className="size-4" />
                  <AlertTitle>{"无法获取版本列表"}</AlertTitle>
                  <AlertDescription>{check.registry_error}</AlertDescription>
                </Alert>
              ) : (
                <>
                  {!check.upgrader && (
                    <Alert>
                      <IconAlertCircle className="size-4" />
                      <AlertTitle>{"后台一键升级未启用"}</AlertTitle>
                      <AlertDescription className="flex items-center gap-2">
                        <code className="rounded bg-muted px-1.5 py-0.5 text-xs">bash scripts/deploy.sh upgrade</code>
                        <CopyButton value="bash scripts/deploy.sh upgrade" className="size-7" />
                      </AlertDescription>
                    </Alert>
                  )}
                  <div className="divide-y rounded-lg border">
                    {check.versions.slice(0, 8).map((v) => (
                      <div key={v} className="flex items-center justify-between px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{fmtVersion(v)}</span>
                          {v === check.latest && <Badge variant="secondary">最新</Badge>}
                          {v === check.current && <Badge variant="outline">当前</Badge>}
                        </div>
                        {check.upgrader && (
                          <Button
                            size="sm"
                            variant={v === check.latest ? "default" : "outline"}
                            disabled={running || v === check.current}
                            onClick={() => setConfirmVersion(v)}
                          >
                            {v === check.current ? "已在此版本" : "升级到此版本"}
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        {/* 升级进度 / 最近结果 */}
        {status && status.state !== "idle" && (
          <div className="px-4 lg:px-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {"升级进度"}
                  <Badge
                    variant={
                      status.state === "success" ? "secondary"
                        : status.state === "failed" ? "destructive" : "default"
                    }
                  >
                    {STATE_TEXT[status.state]}
                  </Badge>
                  {status.target && (
                    <span className="text-sm text-muted-foreground">
                      {"目标 " + fmtVersion(status.target)}
                    </span>
                  )}
                </CardTitle>
                <CardDescription>
                  {running
                    ? "升级期间请勿重复操作；页面偶尔断开属正常（服务逐台重启），会自动恢复"
                    : "最近一次升级的执行记录"}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {(status.steps || []).map((step, i) => (
                    <div key={i} className="flex items-start gap-3 text-sm">
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {fmtTime(step.t)}
                      </span>
                      <span>{step.msg}</span>
                    </div>
                  ))}
                  {running && !(status.steps || []).length && (
                    <p className="text-sm text-muted-foreground">{"等待执行器接手..."}</p>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>

      {/* 升级确认 */}
      <AlertDialog open={!!confirmVersion} onOpenChange={(open) => !open && setConfirmVersion(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{"确认升级到 " + fmtVersion(confirmVersion || "") + " ？"}</AlertDialogTitle>
            <AlertDialogDescription>
              {"将自动完成：拉取镜像 → 数据库增量迁移 → 逐台重启服务。多副本部署近似无感；单副本会中断约 10-30 秒，建议在低峰期执行。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>{"取消"}</AlertDialogCancel>
            <AlertDialogAction onClick={submitUpgrade} disabled={submitting}>
              {submitting ? "提交中..." : "开始升级"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
