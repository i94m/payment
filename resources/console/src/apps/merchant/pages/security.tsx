"use client"

import * as React from "react"
import { accountApi, securityApi } from "@/api"
import type { ApiKeyData, SignDebugResult } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { CopyableText, CopyButton } from "@/components/ui/copy-button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/data-list/confirm-dialog"
import { PanelError } from "@/components/data-list"
import { getErrorMessage } from "@/lib/error"
import { cn } from "@/lib/utils"
import {
  IconCheck, IconCopy, IconEye, IconEyeOff, IconKey, IconRefresh, IconShieldCheck,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"

const DEBUG_PLACEHOLDER = `{
  "pid": "1001",
  "type": "alipay",
  "out_trade_no": "20261119001",
  "name": "VIP会员",
  "money": "1.00",
  "timestamp": "1763558400"
}`

/** 签名方式选项卡片：选中态高亮，未生效算法只保留切换入口。 */
function SignOption({
  value, title, description, active, hint,
}: {
  value: string
  title: string
  description: string
  active: boolean
  hint?: string
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
        active ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50",
      )}
    >
      <RadioGroupItem value={value} id={`sign-${value}`} className="mt-0.5" />
      <div className="grid gap-0.5">
        <span className="flex items-center gap-2 text-sm font-medium">
          {title}
          {active && (
            <Badge
              variant="outline"
              className="border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            >
              {"当前生效"}
            </Badge>
          )}
        </span>
        <span className="text-xs text-muted-foreground">{description}</span>
        {hint && <span className="text-xs text-amber-600 dark:text-amber-500">{hint}</span>}
      </div>
    </label>
  )
}

/** 对接信息一行：标签 + 值 + 可选操作（如密钥的眼睛按钮）+ 复制按钮。
 *  复制目标可单独传入：密钥行按当前「可见状态」复制（脱敏时复制脱敏值）。 */
function IntegrationRow({
  label, value, copyValue, children,
}: {
  label: string
  value: string
  copyValue?: string
  children?: React.ReactNode
}) {
  const { copyToClipboard, isCopied } = useCopyToClipboard()
  const target = copyValue ?? value
  const onCopy = async () => {
    if (!target) return
    if (!(await copyToClipboard(target))) {
      toast.error("复制失败，请手动选中文本复制")
    }
  }
  return (
    <div className="flex items-start gap-3 px-4 py-2.5">
      <span className="w-16 shrink-0 pt-0.5 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-all font-mono text-sm">
        {value || "-"}
      </span>
      <div className="flex shrink-0 items-center">
        {children}
        <Button
          size="icon"
          variant="ghost"
          className="size-8"
          aria-label={`复制${label}`}
          title={`复制${label}`}
          disabled={!target}
          onClick={onCopy}
        >
          {isCopied
            ? <IconCheck className="size-4 text-green-500" />
            : <IconCopy className="size-4" />}
        </Button>
      </div>
    </div>
  )
}

export function SecurityPage() {

  const [apiKey, setApiKey] = React.useState<ApiKeyData | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [loadError, setLoadError] = React.useState<string | null>(null)

  // MD5 密钥默认掩码，点眼睛查看明文
  const [showSecret, setShowSecret] = React.useState(false)
  // 重置 MD5 密钥二次确认
  const [secretResetOpen, setSecretResetOpen] = React.useState(false)
  // 切换签名方式请求中（防连点）
  const [switchBusy, setSwitchBusy] = React.useState(false)

  // 启用 RSA2：已生成过密钥对则直接确认，否则先生成密钥对（私钥仅显示一次）
  const [confirmEnableOpen, setConfirmEnableOpen] = React.useState(false)
  const [confirmGenerateOpen, setConfirmGenerateOpen] = React.useState(false)
  const [confirmDisableOpen, setConfirmDisableOpen] = React.useState(false)

  // 生成的 RSA 密钥对（私钥仅显示一次）
  const [rsaPair, setRsaPair] = React.useState<{ public_key: string; private_key: string } | null>(null)
  // 生成密钥对后是否继续启用 RSA2（首次启用流程）
  const [enableAfterKey, setEnableAfterKey] = React.useState(false)
  // 重置密钥对二次确认
  const [rsaResetOpen, setRsaResetOpen] = React.useState(false)

  // 签名自检
  const [debugOpen, setDebugOpen] = React.useState(false)
  const [debugInput, setDebugInput] = React.useState(DEBUG_PLACEHOLDER)
  const [debugResult, setDebugResult] = React.useState<SignDebugResult | null>(null)
  const [debugBusy, setDebugBusy] = React.useState(false)

  const load = React.useCallback(() => {
    accountApi.apiKey()
      .then((res) => {
        setApiKey(res)
        setLoadError(null)
      })
      .catch((err) => setLoadError(getErrorMessage(err, "接口密钥加载失败")))
      .finally(() => setLoading(false))
  }, [])

  React.useEffect(() => { load() }, [load])

  const rsa2 = !!apiKey?.rsa2_enable
  const secret = apiKey?.api_secret || ""
  const maskedSecret = secret
    ? `${secret.slice(0, 4)}${"*".repeat(Math.max(secret.length - 8, 8))}${secret.slice(-4)}`
    : ""

  // ---- 对接信息（可整段复制给接入方的纯文本）----
  const { copyToClipboard: copyIntegration, isCopied: integrationCopied } = useCopyToClipboard()
  // 域名一律取当前访问页面的 origin：客户从哪个域名登录，拿到的就是哪个域名
  const site = window.location.origin.replace(/\/+$/, "")
  // 业务编码只展示后台标了「测试编码」的那条（由后端挑选，前端不判断用途）；
  // 没有标记时这一行整行不展示，也不回退到正式编码。
  const codeValue = apiKey?.channel_code
    ? (apiKey.channel_code.name
      ? `${apiKey.channel_code.code} - ${apiKey.channel_code.name}`
      : apiKey.channel_code.code)
    : ""
  // 复制跟随当前可见状态：脱敏时复制脱敏值，点眼睛后才复制明文
  const visibleSecret = showSecret ? secret : maskedSecret
  // RSA2 生效时没有可交付的共享密钥（私钥只在商户侧），不能把已停用的 MD5 密钥发出去
  const secretValue = rsa2 ? "RSA2 签名（私钥由接入方自行保管）" : visibleSecret
  const integrationFields: Array<[string, string]> = apiKey ? [
    ["商户名称", apiKey.username],
    ["商户号", String(apiKey.pid)],
    ["API密钥", secretValue],
    // 只有被标记为测试编码时才占一行；缺这一行不影响其它字段与整段复制
    ...(codeValue ? [["业务编码", codeValue] as [string, string]] : []),
    ["下单地址", `${site}/api/orders`],
    ["查询地址", `${site}/api/orders/{trade_no}`],
    ["对接文档", `${site}/docs`],
  ] : []
  const integrationText = apiKey ? [
    "【商户对接参数】",
    ...integrationFields.map(([label, value]) => `${label}：${value}`),
  ].join("\n") : ""

  const doCopyIntegration = async () => {
    if (!integrationText) return
    if (!(await copyIntegration(integrationText))) {
      toast.error("复制失败，请手动选中文本复制")
    }
  }

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

  /** 选择签名方式：MD5 直接确认停用 RSA2；RSA2 需要公钥，未配置时先进配置弹窗 */
  const selectSignType = (next: string) => {
    if (switchBusy || !apiKey || next === (rsa2 ? "RSA2" : "MD5")) return
    if (next === "RSA2") {
      if (apiKey.public_key) {
        setConfirmEnableOpen(true)
      } else {
        setConfirmGenerateOpen(true)
      }
      return
    }
    setConfirmDisableOpen(true)
  }

  const doEnableRsa2 = async () => {
    setSwitchBusy(true)
    try {
      await securityApi.toggleRsa2(true)
      toast.success("已启用 RSA2 签名，MD5 签名已停用")
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setSwitchBusy(false)
    }
  }

  const doDisableRsa2 = async () => {
    setSwitchBusy(true)
    try {
      await securityApi.toggleRsa2(false)
      toast.success("已切回 MD5 签名，RSA2 请求将被拒绝")
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setSwitchBusy(false)
    }
  }

  /** 未生成过密钥对时：生成后弹私钥（仅显示一次），保存后继续启用 */
  const doGenerateAndEnable = async () => {
    const pair = await securityApi.generateRsaPair()
    setRsaPair(pair)
    setEnableAfterKey(true)
  }

  /** 私钥弹窗确认：首次启用流程在此完成启用；重置流程仅关闭弹窗 */
  const confirmPrivateKeySaved = async () => {
    if (!enableAfterKey) {
      setRsaPair(null)
      return
    }
    try {
      await securityApi.toggleRsa2(true)
      setRsaPair(null)
      setEnableAfterKey(false)
      toast.success("已启用 RSA2 签名，MD5 签名已停用")
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, "启用RSA2失败"))
    }
  }

  /** 重置 RSA 密钥对：替换后 RSA2 仍保持开启 */
  const doResetRsa = async () => {
    try {
      const pair = await securityApi.generateRsaPair()
      setEnableAfterKey(false)
      setRsaPair(pair)
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
        <div className="px-4 lg:px-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <IconKey className="size-5 text-primary" />
            {"API 信息"}
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {"开放 API 的接入凭据与签名方式，修改后需同步接入方。"}
          </p>
        </div>

        <div className="space-y-4 px-4 lg:px-6">
          {loadError && <PanelError error={loadError} onRetry={() => { setLoadError(null); load() }} />}

          {loading && !apiKey ? (
            <Skeleton className="h-80 w-full rounded-xl" />
          ) : (
            <>
              {/* 对接信息：整段/逐项复制给接入方的纯文本，密钥按当前可见状态复制 */}
              {apiKey && (
                <Card>
                  <CardHeader>
                    <CardTitle>{"对接信息"}</CardTitle>
                    <CardDescription>
                      {"可直接复制发送给接入方，内容为「字段：值」纯文本（非表格/HTML）。"}
                    </CardDescription>
                    <CardAction>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={doCopyIntegration}
                        disabled={!integrationText}
                      >
                        {integrationCopied
                          ? <IconCheck className="size-4 text-green-500" />
                          : <IconCopy className="size-4" />}
                        {"一键复制全部信息"}
                      </Button>
                    </CardAction>
                  </CardHeader>
                  <CardContent className="p-0">
                    <div>
                      <div className="px-4 py-2 text-xs font-medium text-muted-foreground">
                        {"【商户对接参数】"}
                      </div>
                      {integrationFields.map(([label, value]) => {
                        // RSA2 下没有可交付的共享密钥，这一行不可单独复制
                        const cannotCopy = label === "API密钥" && rsa2
                        return (
                          <IntegrationRow
                            key={label}
                            label={label}
                            value={value}
                            copyValue={cannotCopy ? "" : undefined}
                          >
                            {label === "API密钥" && !rsa2 && (
                              <Button
                                size="icon"
                                variant="ghost"
                                className="size-8"
                                aria-label={showSecret ? "隐藏密钥" : "显示密钥"}
                                title={showSecret ? "隐藏密钥" : "查看明文"}
                                onClick={() => setShowSecret((v) => !v)}
                              >
                                {showSecret
                                  ? <IconEyeOff className="size-4" />
                                  : <IconEye className="size-4" />}
                              </Button>
                            )}
                          </IntegrationRow>
                        )
                      })}
                    </div>
                    <div className="space-y-1 px-4 py-2.5 text-xs text-muted-foreground">
                      <p>
                        {apiKey.channel_code
                          ? "下单时携带 channel_code（即上方业务编码），只在该编码绑定的通道内分流；不传则按平台默认路由。"
                          : "下单可携带 channel_code 指定通道范围（业务编码由运营另行提供）；不传则按平台默认路由。"}
                        {rsa2
                          ? "当前为 RSA2 签名，平台不保存私钥，对接信息中不包含密钥明文。"
                          : "API 密钥默认脱敏，点眼睛查看明文后复制才会带明文。"}
                      </p>
                    </div>
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle>{"接入信息"}</CardTitle>
                  <CardDescription>{"商户号、签名方式与当前生效的密钥。"}</CardDescription>
                  {/* 签名自检只对 MD5 有意义：RSA2 的期望签名只能由商户私钥生成，平台给不出 */}
                  {!rsa2 && (
                    <CardAction>
                      <Button size="sm" variant="outline" onClick={() => setDebugOpen(true)}>
                        <IconShieldCheck className="size-4" />
                        {"签名自检"}
                      </Button>
                    </CardAction>
                  )}
                </CardHeader>
                <CardContent className="space-y-5">
                  {/* 签名方式切换 */}
                  <div className="space-y-2">
                    <Label>{"签名方式"}</Label>
                    <RadioGroup
                      value={rsa2 ? "RSA2" : "MD5"}
                      onValueChange={selectSignType}
                      disabled={switchBusy}
                      className="grid gap-3 sm:grid-cols-2"
                    >
                      <SignOption
                        value="MD5"
                        title="MD5 密钥签名"
                        description="对称密钥，开箱即用。"
                        active={!rsa2}
                      />
                      <SignOption
                        value="RSA2"
                        title="RSA2 非对称签名"
                        description="非对称密钥，需配置公钥。"
                        active={rsa2}
                        hint={!rsa2 && !apiKey?.public_key ? "选择后会先引导配置公钥" : undefined}
                      />
                    </RadioGroup>
                    <p className="text-xs font-medium text-amber-600 dark:text-amber-400">
                      {`注意：当前仅接受 sign_type=${rsa2 ? "RSA2" : "MD5"}。`}
                    </p>
                  </div>

                  {/* 身份：pid 是接入最常用的信息，用独立展示条放大并支持一键复制 */}
                  <div className="grid gap-2">
                    <Label>{"商户ID (pid)"}</Label>
                    <div className="flex items-center gap-2">
                      <Input
                        readOnly
                        value={String(apiKey?.pid ?? "")}
                        className="font-mono text-sm"
                        onFocus={(e) => e.currentTarget.select()}
                        onClick={(e) => e.currentTarget.select()}
                      />
                      <CopyButton
                        value={String(apiKey?.pid ?? "")}
                        aria-label="复制商户ID"
                        className="size-8"
                      />
                    </div>
                  </div>

                  {/* 只展示当前生效算法的凭据 */}
                  {!rsa2 ? (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <Label>{"MD5 密钥"}</Label>
                        <Button
                          size="sm"
                          variant="outline"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setSecretResetOpen(true)}
                        >
                          <IconRefresh className="size-4" />
                          {"重置密钥"}
                        </Button>
                      </div>
                      <div className="flex items-center gap-2">
                        <Input
                          readOnly
                          value={showSecret ? secret : maskedSecret}
                          placeholder="加载中"
                          className="min-w-0 flex-1 font-mono text-xs"
                        />
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={showSecret ? "隐藏密钥" : "显示密钥"}
                          onClick={() => setShowSecret((v) => !v)}
                        >
                          {showSecret ? <IconEyeOff className="size-4" /> : <IconEye className="size-4" />}
                        </Button>
                        <CopyButton value={secret} />
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <Label>{"商户公钥"}</Label>
                          {!!apiKey?.public_key && <CopyButton value={apiKey.public_key} />}
                        </div>
                        <Textarea
                          readOnly
                          value={apiKey?.public_key || ""}
                          rows={3}
                          placeholder="暂未生成"
                          className="resize-none field-sizing-fixed break-all bg-muted font-mono text-xs"
                        />
                        <p className="text-xs text-muted-foreground">{"用于平台验证你的请求签名。"}</p>
                      </div>

                      <div className="space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <Label>{"平台公钥"}</Label>
                          {!!apiKey?.platform_public_key && <CopyButton value={apiKey.platform_public_key} />}
                        </div>
                        <Textarea
                          readOnly
                          value={apiKey?.platform_public_key || ""}
                          rows={3}
                          placeholder="暂未生成"
                          className="resize-none field-sizing-fixed break-all bg-muted font-mono text-xs"
                        />
                        <p className="text-xs text-muted-foreground">{"用于验证平台通知签名。"}</p>
                      </div>

                      <div className="flex items-center justify-between gap-3 border-t pt-4">
                        <p className="text-xs text-muted-foreground">{"私钥仅生成时显示一次，平台不保存。"}</p>
                        <Button
                          size="sm"
                          variant="outline"
                          className="shrink-0 text-destructive hover:text-destructive"
                          onClick={() => setRsaResetOpen(true)}
                        >
                          <IconRefresh className="size-4" />
                          {"重置密钥对"}
                        </Button>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            </>
          )}
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
        onConfirm={doResetRsa}
      />

      <ConfirmDialog
        open={confirmEnableOpen}
        onOpenChange={setConfirmEnableOpen}
        title={"启用RSA2签名"}
        description={"启用后 MD5 签名立即失效，所有接入必须改用 sign_type=RSA2，确定继续？"}
        cancelLabel={"取消"}
        confirmLabel={"启用RSA2"}
        onConfirm={doEnableRsa2}
      />

      <ConfirmDialog
        open={confirmDisableOpen}
        onOpenChange={setConfirmDisableOpen}
        title={"切回MD5签名"}
        description={"切回后所有 RSA2 请求将被拒绝，需要接入方改回 sign_type=MD5，确定继续？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"切回MD5"}
        onConfirm={doDisableRsa2}
      />

      <ConfirmDialog
        open={confirmGenerateOpen}
        onOpenChange={setConfirmGenerateOpen}
        title={"启用RSA2签名"}
        description={"将生成新的 RSA 密钥对并启用 RSA2：启用后 MD5 立即失效，私钥仅显示一次，请及时保存。"}
        cancelLabel={"取消"}
        confirmLabel={"生成并启用"}
        onConfirm={doGenerateAndEnable}
      />

      {/* 私钥（仅显示一次） */}
      <Dialog open={!!rsaPair} onOpenChange={(open) => !open && setRsaPair(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"商户私钥（仅显示一次）"}</DialogTitle>
            <DialogDescription className="text-destructive font-medium">
              {"请及时复制保存商户私钥！当前窗口关闭后，无法再次查询商户私钥，本站也不会保存。如遗失商户私钥，可重新生成密钥对进行替换。"}
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
            <Button onClick={confirmPrivateKeySaved}>
              {enableAfterKey ? "我已保存，启用 RSA2" : "我已保存，关闭"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 签名自检 */}
      <Dialog open={debugOpen} onOpenChange={setDebugOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"签名自检"}</DialogTitle>
            <DialogDescription>
              {"粘贴你准备发送的参数（无需 sign / sign_type），这里给出平台实际使用的签名原文与当前生效算法的期望签名。"}
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
    </div>
  )
}
