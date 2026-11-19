"use client"

import * as React from "react"
import { accountApi, securityApi } from "@/api"
import type { ApiKeyData, SignDebugResult } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { CopyableText, CopyButton } from "@/components/ui/copy-button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/data-list/confirm-dialog"
import { PanelError } from "@/components/data-list"
import { getErrorMessage } from "@/lib/error"
import {
  IconEye, IconEyeOff, IconKey, IconRefresh, IconShieldCheck, IconWand,
} from "@tabler/icons-react"
import { toast } from "sonner"

const DEBUG_PLACEHOLDER = `{
  "pid": "1001",
  "type": "alipay",
  "out_trade_no": "20261119001",
  "name": "VIP会员",
  "money": "1.00",
  "timestamp": "1763558400"
}`

export function SecurityPage() {

  const [apiKey, setApiKey] = React.useState<ApiKeyData | null>(null)
  // MD5 密钥默认掩码，点眼睛查看明文
  const [showSecret, setShowSecret] = React.useState(false)
  // 重置 MD5 密钥二次确认
  const [secretResetOpen, setSecretResetOpen] = React.useState(false)
  // RSA2 开关请求中（防连点）
  const [rsa2Busy, setRsa2Busy] = React.useState(false)
  // 重置密钥对二次确认（首次生成无旧密钥可失效，直接生成）
  const [rsaResetOpen, setRsaResetOpen] = React.useState(false)
  // 生成的 RSA 密钥对（私钥仅显示一次）
  const [rsaPair, setRsaPair] = React.useState<{ public_key: string; private_key: string } | null>(null)
  // 商户公钥手动编辑（支持 OpenSSL/代码生成的密钥对上传）
  const [pubEdit, setPubEdit] = React.useState("")
  // 签名自检
  const [debugOpen, setDebugOpen] = React.useState(false)
  const [debugInput, setDebugInput] = React.useState(DEBUG_PLACEHOLDER)
  const [debugResult, setDebugResult] = React.useState<SignDebugResult | null>(null)
  const [debugBusy, setDebugBusy] = React.useState(false)
  const [loadError, setLoadError] = React.useState<string | null>(null)

  const load = React.useCallback(() => {
    accountApi.apiKey()
      .then((res) => {
        setApiKey(res)
        setPubEdit(res.public_key || "")
        setLoadError(null)
      })
      .catch((err) => setLoadError(getErrorMessage(err, "接口密钥加载失败")))
  }, [])

  React.useEffect(() => { load() }, [load])

  const secret = apiKey?.api_secret || ""
  const maskedSecret = secret
    ? `${secret.slice(0, 4)}${"*".repeat(Math.max(secret.length - 8, 8))}${secret.slice(-4)}`
    : ""

  /** 重置 MD5 密钥：旧密钥立即失效，接入方需同步更换 */
  const doResetSecret = async () => {
    try {
      const res = await securityApi.regenerateApiSecret()
      setApiKey((d) => (d ? { ...d, api_secret: res.api_secret } : d))
      setShowSecret(true)
      toast.success("MD5 密钥已重置，请同步更新接入代码")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const doToggleRsa2 = async (enable: boolean) => {
    setRsa2Busy(true)
    try {
      await securityApi.toggleRsa2(enable)
      setApiKey((d) => (d ? { ...d, rsa2_enable: enable } : d))
      toast.success(enable ? "已开启 RSA2 签名" : "已关闭 RSA2 签名")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setRsa2Busy(false)
    }
  }

  const doGenerateRsa = async () => {
    try {
      const pair = await securityApi.generateRsaPair()
      // 后端生成即保存公钥（API 立即可用）；私钥仅显示一次，弹窗供商户完整复制
      toast.success("密钥对已生成并保存")
      setRsaPair(pair)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  /** 已配置公钥时点「重置RSA密钥对」先确认，确认后才生成替换；首次生成直接执行 */
  const generateRsa = () => {
    if (apiKey?.public_key) {
      setRsaResetOpen(true)
      return
    }
    void doGenerateRsa()
  }

  /** 保存商户公钥（手动上传，OpenSSL/代码生成场景）；清空保存即清除公钥 */
  const savePublicKey = async () => {
    try {
      await accountApi.setPublicKey(pubEdit.trim())
      toast.success(pubEdit.trim() ? "商户公钥已保存" : "商户公钥已清除")
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const doSignDebug = async () => {
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(debugInput)
    } catch {
      toast.error("参数必须是合法 JSON")
      return
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      toast.error("参数必须是 JSON 对象")
      return
    }
    setDebugBusy(true)
    try {
      setDebugResult(await securityApi.signDebug(parsed))
    } catch (err) {
      toast.error(getErrorMessage(err, "签名失败"))
    } finally {
      setDebugBusy(false)
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-3xl">
        {/* 密钥 */}
        <div className="px-4 lg:px-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <IconKey className="size-4 text-primary" />
            {"接口密钥"}
          </h2>
        </div>
        <div className="px-4 lg:px-6 space-y-4">
          {loadError && <PanelError error={loadError} onRetry={() => { setLoadError(null); load() }} />}
          <div className="grid gap-1.5">
            <Label>{"商户ID (pid)"}</Label>
            <CopyableText value={String(apiKey?.pid ?? "")} className="font-mono break-all" />
          </div>

          {/* 默认算法：MD5 */}
          <div className="grid gap-4 border-t pt-4">
            <div>
              <h4 className="text-sm font-medium">{"MD5 密钥（默认签名方式）"}</h4>
              <p className="mt-1 text-xs text-muted-foreground">
                {"开放 API 默认使用 MD5 签名：sign = md5(签名原文 + 密钥)，32 位小写。所有接口只需 pid + 密钥即可对接。"}
              </p>
            </div>

            <div className="grid gap-1.5">
              <Label>{"MD5 密钥"}</Label>
              <div className="flex items-center gap-2">
                <Input
                  readOnly
                  value={showSecret ? secret : maskedSecret}
                  placeholder="加载中"
                  className="min-w-0 flex-1 font-mono text-xs"
                />
                <Button
                  size="icon"
                  variant="outline"
                  aria-label={showSecret ? "隐藏密钥" : "显示密钥"}
                  onClick={() => setShowSecret((v) => !v)}
                >
                  {showSecret ? <IconEyeOff className="size-4" /> : <IconEye className="size-4" />}
                </Button>
                <CopyButton value={secret} />
              </div>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {"密钥泄漏等于他人可代你下单/退款，请只在服务端使用；无需可随时重置。"}
                </p>
                <Button size="sm" variant="outline" onClick={() => setSecretResetOpen(true)}>
                  <IconRefresh className="size-4" />
                  {"重置密钥"}
                </Button>
              </div>
            </div>

            <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setDebugOpen(true)}>
              <IconShieldCheck className="size-4" />
              {"签名自检"}
            </Button>
          </div>

          {/* 可选算法：RSA2 */}
          <div className="grid gap-4 border-t pt-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h4 className="text-sm font-medium">{"RSA2 签名（可选）"}</h4>
                <p className="mt-1 text-xs text-muted-foreground">
                  {
                    "需要抗抵赖（争议取证）时开启：使用你的私钥签名，平台只保存公钥。" +
                    "开启后接入方可在 sign_type=RSA2 时调用；MD5 始终可用。"
                  }
                </p>
              </div>
              <Switch
                checked={!!apiKey?.rsa2_enable}
                disabled={rsa2Busy || !apiKey || (!apiKey.rsa2_enable && !apiKey.public_key)}
                onCheckedChange={(v) => void doToggleRsa2(v)}
              />
            </div>

            {apiKey?.rsa2_enable && (
              <div className="grid gap-1.5">
                <div className="flex items-center justify-between">
                  <Label>{"平台公钥"}</Label>
                  {!!apiKey.platform_public_key && <CopyButton value={apiKey.platform_public_key} />}
                </div>
                <Textarea
                  readOnly
                  value={apiKey.platform_public_key || ""}
                  rows={3}
                  placeholder="暂未生成"
                  className="resize-none field-sizing-fixed break-all bg-muted font-mono text-xs"
                />
                <p className="text-xs text-muted-foreground">
                  {"验证平台异步通知（Webhook）的 RSA2 签名用，复制给开发者"}
                </p>
              </div>
            )}

            <div className="grid gap-1.5">
              <Label>{"商户公钥"}</Label>
              <Textarea
                value={pubEdit}
                onChange={(e) => setPubEdit(e.target.value)}
                rows={3}
                placeholder="点击下方「一键生成密钥对」或粘贴自行生成的公钥"
                className="resize-none field-sizing-fixed break-all bg-muted font-mono text-xs"
              />
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {"支持单行 Base64 或 PEM 格式，清空后保存即清除公钥并关闭 RSA2"}
                </p>
                <Button size="sm" variant="outline" disabled={pubEdit.trim() === (apiKey?.public_key || "")}
                        onClick={savePublicKey}>
                  {"保存公钥"}
                </Button>
              </div>
            </div>

            <div className="grid gap-1.5">
              <Label>{"商户私钥"}</Label>
              <p className="rounded-lg bg-muted/50 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
                {
                  "出于安全考虑，商户私钥仅在生成密钥对时弹出显示一次，本站不会保存。" +
                  "请生成后立即复制并妥善保管，切勿泄露给他人；如遗失，可点击下方「重置RSA密钥对」重新生成。"
                }
              </p>
            </div>
            <Button size="sm" variant="outline" className="justify-self-start" onClick={generateRsa}>
              <IconWand className="size-4" />
              {apiKey?.public_key ? "重置RSA密钥对" : "一键生成密钥对"}
            </Button>
            {!apiKey?.rsa2_enable && (
              <p className="text-xs text-muted-foreground">
                {"配置公钥后即可打开上方开关；未开启时 sign_type=RSA2 的请求会被拒绝。"}
              </p>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={secretResetOpen}
        onOpenChange={setSecretResetOpen}
        title={"重置MD5密钥"}
        description={"重置后原密钥立即失效，所有使用 MD5 签名的接入需同步更换新密钥，确定继续？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定重置"}
        onConfirm={doResetSecret}
      />

      <ConfirmDialog
        open={rsaResetOpen}
        onOpenChange={setRsaResetOpen}
        title={"重置RSA密钥对"}
        description={"重置后原密钥对立即失效，使用RSA2签名的接入需更换新密钥对，确定继续？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定重置"}
        onConfirm={doGenerateRsa}
      />

      {/* 签名自检 */}
      <Dialog open={debugOpen} onOpenChange={setDebugOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"签名自检"}</DialogTitle>
            <DialogDescription>
              {"粘贴你准备发送的参数（无需 sign / sign_type），这里给出平台实际使用的签名原文与期望签名。"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Textarea
              value={debugInput}
              onChange={(e) => setDebugInput(e.target.value)}
              rows={8}
              className="resize-none field-sizing-fixed font-mono text-xs"
            />
            <Button size="sm" disabled={debugBusy} onClick={doSignDebug}>
              {"生成签名"}
            </Button>
            {debugResult && (
              <div className="space-y-3 border-t pt-3">
                <div className="grid gap-1.5">
                  <div className="flex items-center justify-between">
                    <Label>{"签名原文"}</Label>
                    <CopyButton value={debugResult.sign_content} />
                  </div>
                  <Textarea
                    readOnly
                    value={debugResult.sign_content}
                    rows={3}
                    className="resize-none field-sizing-fixed break-all bg-muted font-mono text-xs"
                  />
                </div>
                {Object.entries(debugResult.algorithms).map(([algo, info]) => (
                  <div key={algo} className="grid gap-1.5">
                    <Label>{`${algo} 期望签名`}</Label>
                    {info.sign ? (
                      <CopyableText value={info.sign} className="font-mono break-all" />
                    ) : (
                      <p className="text-xs text-muted-foreground">{info.note || "-"}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDebugOpen(false)}>{"关闭"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 私钥（仅显示一次） */}
      <Dialog open={!!rsaPair} onOpenChange={(open) => !open && setRsaPair(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"商户私钥（仅显示一次）"}</DialogTitle>
            <DialogDescription className="text-destructive font-medium">
              {"请及时复制保存商户私钥！当前窗口关闭后，无法再次查询商户私钥，本站也不会保存。如遗失商户私钥，可重新生成进行替换。"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-start gap-2">
            <Textarea
              readOnly
              value={rsaPair?.private_key || ""}
              rows={6}
              className="min-w-0 flex-1 resize-none field-sizing-fixed break-all font-mono text-xs"
            />
            <CopyButton value={rsaPair?.private_key || ""} />
          </div>
          <DialogFooter>
            <Button onClick={() => setRsaPair(null)}>{"我已保存，关闭"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
