"use client"

import * as React from "react"
import { accountApi, securityApi } from "@/api"
import type { ApiKeyData } from "@/api"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { CopyableText, CopyButton } from "@/components/ui/copy-button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/data-list/confirm-dialog"
import { PanelError } from "@/components/data-list"
import { getErrorMessage } from "@/lib/error"
import { IconKey, IconWand } from "@tabler/icons-react"
import { toast } from "sonner"

export function SecurityPage() {

  const [apiKey, setApiKey] = React.useState<ApiKeyData | null>(null)
  // 重置密钥对二次确认（首次生成无旧密钥可失效，直接生成）
  const [rsaResetOpen, setRsaResetOpen] = React.useState(false)
  // 生成的 RSA 密钥对（私钥仅显示一次）
  const [rsaPair, setRsaPair] = React.useState<{ public_key: string; private_key: string } | null>(null)
  // 商户公钥手动编辑（支持 OpenSSL/代码生成的密钥对上传）
  const [pubEdit, setPubEdit] = React.useState("")
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

          <div className="grid gap-4 border-t pt-4">
            <h4 className="text-sm font-medium">{"RSA2 密钥对（开放 API 签名）"}</h4>

            <div className="grid gap-1.5">
              <div className="flex items-center justify-between">
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
              <p className="text-xs text-muted-foreground">
                {"验证平台异步通知（Webhook）签名用，接入时复制给开发者"}
              </p>
            </div>

            <div className="grid gap-1.5">
              <Label>{"商户公钥"}</Label>
              <Textarea
                value={pubEdit}
                onChange={(e) => setPubEdit(e.target.value)}
                rows={3}
                placeholder="暂未配置，点击下方「一键生成密钥对」或粘贴自行生成的公钥"
                className="resize-none field-sizing-fixed break-all bg-muted font-mono text-xs"
              />
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {"配置后才能调用开放 API；支持单行 Base64 或 PEM 格式，清空后保存即清除"}
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
          </div>
        </div>
      </div>

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
