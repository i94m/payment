"use client"

import * as React from "react"
import { accountApi } from "@/api"
import type { ApiKeyData, MerchantChannelCode } from "@/api"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { CopyButton } from "@/components/ui/copy-button"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { PanelError } from "@/components/data-list"
import { PayTypeLabel } from "@/components/pay-icon"
import { getErrorMessage } from "@/lib/error"
import { IconCheck, IconCopy, IconKey } from "@tabler/icons-react"
import { toast } from "sonner"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"

/** 对接信息一行：标签 + 值 + 可选操作 + 复制按钮。
 *  复制目标可单独传入（如 RSA2 下密钥行不可复制时传空串），默认复制展示值。 */
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
    <div className="flex items-start gap-2 py-0.5">
      <span className="w-14 shrink-0 pt-0.5 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-all font-mono text-[13px]">
        {value || "-"}
      </span>
      <div className="flex shrink-0 items-center">
        {children}
        <Button
          size="icon"
          variant="ghost"
          className="size-7"
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

/** 费率来源说明：商户端看到的是该商户实际生效的费率，不是「编码上写的那个数」 */
const FEE_SOURCE_LABEL: Record<MerchantChannelCode["fee_rate_source"], string> = {
  merchant: "我的专属费率（优先于通道费率）",
  channel: "按该编码内实际走的上游通道费率",
  none: "",
}

/** 费率展示：单一费率或区间（编码内通道费率不一致时给区间，不假装只有一个值） */
function feeRateText(c: MerchantChannelCode) {
  if (!c.fee_rate_min) return ""
  return c.fee_rate_min === c.fee_rate_max
    ? `${c.fee_rate_min}%`
    : `${c.fee_rate_min}% ~ ${c.fee_rate_max}%`
}

/** 限额展示：后端已按「0=不限」归一（`不限` / `≥ 5.00` / `1.00 ~ 9.00`） */
function amountLimitText(c: MerchantChannelCode) {
  const text = c.amount_text || "不限"
  return text === "不限" ? text : `${text} 元`
}

export function SecurityPage() {

  const [apiKey, setApiKey] = React.useState<ApiKeyData | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [loadError, setLoadError] = React.useState<string | null>(null)

  // 支付通道：所有启用中的通道编码 + 各自可用的支付方式
  const [channels, setChannels] = React.useState<MerchantChannelCode[]>([])
  const [channelsLoading, setChannelsLoading] = React.useState(true)
  const [channelsError, setChannelsError] = React.useState<string | null>(null)

  const load = React.useCallback(() => {
    accountApi.apiKey()
      .then((res) => {
        setApiKey(res)
        setLoadError(null)
      })
      .catch((err) => setLoadError(getErrorMessage(err, "接口密钥加载失败")))
      .finally(() => setLoading(false))
  }, [])

  const loadChannels = React.useCallback(() => {
    accountApi.channelCodes()
      .then((res) => {
        setChannels(res)
        setChannelsError(null)
      })
      .catch((err) => setChannelsError(getErrorMessage(err, "支付通道加载失败")))
      .finally(() => setChannelsLoading(false))
  }, [])

  React.useEffect(() => { load() }, [load])
  React.useEffect(() => { loadChannels() }, [loadChannels])

  const rsa2 = !!apiKey?.rsa2_enable
  const secret = apiKey?.api_secret || ""

  // ---- 对接信息（可整段复制给接入方的纯文本）----
  const { copyToClipboard: copyIntegration, isCopied: integrationCopied } = useCopyToClipboard()
  // 域名一律取当前访问页面的 origin：客户从哪个域名登录，拿到的就是哪个域名
  const site = window.location.origin.replace(/\/+$/, "")
  // 默认通道由后台指定（全平台唯一），只做展示：让接入方知道平台当前主用哪条编码。
  // 展示的是**通道编码**本身（下游下单直接当 channel_code 传），不是通道名称。
  // 没有默认编码时这一行整行不展示。
  const defaultChannel = apiKey?.default_channel
  const channelValue = defaultChannel?.code ?? ""
  // 商户端密钥始终明文展示，逐项复制与整段复制都直接用原始值，不做任何掩码
  // RSA2 生效时没有可交付的共享密钥（私钥只在商户侧），不能把已停用的 MD5 密钥发出去
  const secretValue = rsa2 ? "RSA2 签名（私钥由接入方自行保管）" : secret
  const integrationFields: Array<[string, string]> = apiKey ? [
    ["商户名称", apiKey.username],
    ["商户号", String(apiKey.pid)],
    ["API密钥", secretValue],
    // 没有默认通道时不占这一行；缺这一行不影响其它字段与整段复制
    ...(channelValue ? [["默认通道", channelValue] as [string, string]] : []),
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

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-3xl">
        <div className="px-4 lg:px-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <IconKey className="size-5 text-primary" />
            {"API 信息"}
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {"开放 API 的接入凭据与可用支付通道。"}
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
                      {"商户对接参数可一键复制发送给接入方。"}
                    </CardDescription>
                    <CardAction>
                      <Button
                        size="sm"
                        onClick={doCopyIntegration}
                        disabled={!integrationText}
                      >
                        {integrationCopied
                          ? <IconCheck className="size-4 text-green-500" />
                          : <IconCopy className="size-4" />}
                        {"一键复制对接参数"}
                      </Button>
                    </CardAction>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {/* 商户对接参数模块：与后台数据统计页「订单成功率」同为卡片内的浅色分区，
                        标题只出现在模块里，一键复制的纯文本仍带「【商户对接参数】」 */}
                    <div className="rounded-lg border bg-muted/30 p-3">
                      <div className="text-sm font-semibold text-foreground">{"商户对接参数"}</div>
                      <div className="mt-2 space-y-0">
                        {integrationFields.map(([label, value]) => {
                          // RSA2 下没有可交付的共享密钥，这一行不可单独复制
                          const cannotCopy = label === "API密钥" && rsa2
                          return (
                            <IntegrationRow
                              key={label}
                              label={label}
                              value={value}
                              copyValue={cannotCopy ? "" : undefined}
                            />
                          )
                        })}
                      </div>
                    </div>

                    {/* 支付通道同为其下的子模块：所有启用中的通道编码，下单时作为 channel_code 传入 */}
                    <div className="rounded-lg border bg-muted/30 p-3">
                      <div className="text-sm font-semibold text-foreground">{"支付通道"}</div>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {"下单时把编码作为 channel_code 传入即可限定路由；单笔金额超出「单笔限额」会被直接拒绝，费率按该商户实际生效值展示。"}
                      </p>
                      <div className="mt-2">
                        {channelsError ? (
                          <PanelError error={channelsError} onRetry={() => { setChannelsError(null); loadChannels() }} />
                        ) : channelsLoading ? (
                          <div className="space-y-2">
                            <Skeleton className="h-9 w-full rounded-md" />
                            <Skeleton className="h-9 w-full rounded-md" />
                          </div>
                        ) : channels.length === 0 ? (
                          <p className="py-4 text-center text-xs text-muted-foreground">
                            {"当前没有已开启的通道编码，请联系运营开通。"}
                          </p>
                        ) : (
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>{"通道编码"}</TableHead>
                                <TableHead>{"名称"}</TableHead>
                                <TableHead className="text-right">{"单笔限额"}</TableHead>
                                <TableHead className="text-right">{"费率"}</TableHead>
                                <TableHead>{"支付方式"}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {channels.map((c) => (
                                <TableRow key={c.code}>
                                  <TableCell>
                                    <div className="flex items-center gap-1">
                                      <span className="font-mono text-sm font-medium">{c.code}</span>
                                      <CopyButton
                                        value={c.code}
                                        aria-label={`复制编码 ${c.code}`}
                                        title={`复制编码 ${c.code}`}
                                        className="size-6"
                                      />
                                    </div>
                                  </TableCell>
                                  <TableCell>
                                    <span className="text-sm">{c.name || "—"}</span>
                                    {c.remark && (
                                      <span className="mt-0.5 block text-xs text-muted-foreground">{c.remark}</span>
                                    )}
                                  </TableCell>
                                  <TableCell className="text-right">
                                    <span className="text-sm tabular-nums">{amountLimitText(c)}</span>
                                  </TableCell>
                                  <TableCell className="text-right">
                                    <span
                                      className="text-sm tabular-nums"
                                      title={FEE_SOURCE_LABEL[c.fee_rate_source] || undefined}
                                    >
                                      {feeRateText(c) || "—"}
                                    </span>
                                  </TableCell>
                                  <TableCell>
                                    {c.pay_types.length > 0 ? (
                                      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                                        {c.pay_types.map((t) => (
                                          <PayTypeLabel key={t.code} code={t.code} icon={t.icon} name={t.name} />
                                        ))}
                                      </span>
                                    ) : (
                                      <span className="text-xs text-muted-foreground">{"暂不可用"}</span>
                                    )}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        )}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
