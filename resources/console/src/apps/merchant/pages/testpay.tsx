"use client"

import * as React from "react"
import { Link } from "react-router-dom"
import { testPayApi } from "@/api"
import type { TestPayResult, TestPayType } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Field, FieldDescription, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { CopyableInput } from "@/components/ui/copy-button"
import { PayIcon } from "@/components/pay-icon"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { numStr } from "@/lib/form-schema"
import { getErrorMessage } from "@/lib/error"
import { IconFlask, IconExternalLink, IconListDetails } from "@tabler/icons-react"
import { toast } from "sonner"

// 金额范围与后端 /testpay/submit 口径一致
const testSchema = z.object({
  type: z.string().min(1, "请选择支付方式").default(""),
  name: z.string().default("支付测试"),
  amount: numStr("测试金额", { min: 0.01, max: 1000 }),
})

type TestForm = z.input<typeof testSchema>

export function TestPayPage() {
  const [types, setTypes] = React.useState<TestPayType[]>([])
  const [typesError, setTypesError] = React.useState<string | null>(null)
  const [typesLoading, setTypesLoading] = React.useState(true)
  const [submitting, setSubmitting] = React.useState(false)
  const [result, setResult] = React.useState<TestPayResult | null>(null)

  const form = useForm<TestForm, unknown, z.output<typeof testSchema>>({
    resolver: zodResolver(testSchema),
    defaultValues: { type: "", name: "支付测试", amount: "1.00" },
  })

  React.useEffect(() => {
    testPayApi.types()
      .then((list) => {
        setTypes(list)
        if (list.length > 0) form.setValue("type", list[0].code)
        setTypesError(null)
      })
      .catch((err) => setTypesError(getErrorMessage(err, "支付方式加载失败")))
      .finally(() => setTypesLoading(false))
  }, [form])

  const submit = form.handleSubmit(async (values) => {
    setSubmitting(true)
    setResult(null)
    try {
      const data = await testPayApi.submit({
        type: values.type,
        amount: values.amount,
        name: values.name || "支付测试",
      })
      setResult(data)
      const url = /^https?:\/\//.test(data.pay_info || "") ? data.pay_info : ""
      if (url && data.pay_type !== "html") {
        window.open(url, "_blank")
        toast.success("测试单已创建，已打开支付页")
      } else {
        toast.success("测试单已创建")
      }
    } catch (err) {
      toast.error(getErrorMessage(err, "测试支付失败"))
    } finally {
      setSubmitting(false)
    }
  })

  const payUrl = result && /^https?:\/\//.test(result.pay_info || "") && result.pay_type !== "html"
    ? result.pay_info
    : ""
  const rawPayInfo = result && !payUrl ? result.pay_info : ""

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-3xl">
        <div className="px-4 lg:px-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <IconFlask className="size-5" />
            {"测试支付"}
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            {"用一笔真实订单验证通道路由、上游下单和回调处理：测试单会出现在订单记录里（默认全部类型即可看到，也可筛「测试单」），但不计入余额、结算和任何统计。"}
          </p>
        </div>

        <div className="px-4 lg:px-6 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{"发起测试"}</CardTitle>
              <CardDescription>
                {"支付完成后回到「订单记录 → 测试单」，状态变为已支付即说明回调链路正常。"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={submit} className="space-y-3">
                <Controller
                  name="type"
                  control={form.control}
                  render={({ field, fieldState }) => (
                    <Field data-invalid={fieldState.invalid}>
                      <FieldLabel>{"支付方式"}<RequiredMark /></FieldLabel>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        disabled={typesLoading || types.length === 0}
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder={typesLoading ? "加载中…" : "请选择支付方式"} />
                        </SelectTrigger>
                        <SelectContent>
                          {types.map((t) => (
                            <SelectItem key={t.code} value={t.code}>
                              <span className="flex items-center gap-2">
                                <PayIcon code={t.code} icon={t.icon || ""} />
                                {t.name}
                              </span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {typesError && <FieldDescription className="text-destructive">{typesError}</FieldDescription>}
                      {!typesLoading && types.length === 0 && !typesError && (
                        <FieldDescription>{"当前没有可用支付方式，请联系管理员配置通道"}</FieldDescription>
                      )}
                      {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                    </Field>
                  )}
                />
                <Controller
                  name="name"
                  control={form.control}
                  render={({ field }) => (
                    <Field>
                      <FieldLabel htmlFor={field.name}>{"订单名称"}</FieldLabel>
                      <Input id={field.name} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} />
                    </Field>
                  )}
                />
                <Controller
                  name="amount"
                  control={form.control}
                  render={({ field, fieldState }) => (
                    <Field data-invalid={fieldState.invalid}>
                      <FieldLabel htmlFor={field.name}>{"测试金额"}<RequiredMark /></FieldLabel>
                      <Input
                        id={field.name}
                        type="number" min="0.01" max="1000" step="0.01"
                        value={field.value ?? ""}
                        onChange={(e) => field.onChange(e.target.value)}
                        aria-invalid={fieldState.invalid || undefined}
                      />
                      <FieldDescription>{"0.01 ~ 1000 元。会真实向上游发起一笔支付，测试单不产生任何资金流水。"}</FieldDescription>
                      {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                    </Field>
                  )}
                />
                <Button type="submit" disabled={submitting || types.length === 0}>
                  {submitting ? "发起中…" : "发起测试支付"}
                </Button>
              </form>
            </CardContent>
          </Card>

          {result && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  {"测试单已创建"}
                  <Badge variant="secondary">{"测试单"}</Badge>
                </CardTitle>
                <CardDescription>{`支付动作：${result.pay_type}`}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Field>
                  <FieldLabel>{"测试单号"}</FieldLabel>
                  <CopyableInput value={result.trade_no} className="w-full font-mono text-xs" />
                </Field>
                {payUrl && (
                  <Button asChild variant="outline">
                    <a href={payUrl} target="_blank" rel="noreferrer">
                      <IconExternalLink className="size-4" />
                      {"重新打开支付页"}
                    </a>
                  </Button>
                )}
                {rawPayInfo && (
                  <Field>
                    <FieldLabel>{result.pay_type === "qrcode" ? "二维码内容" : "支付内容"}</FieldLabel>
                    <pre className="max-h-40 overflow-auto rounded-md border bg-muted/40 p-2 text-xs break-all whitespace-pre-wrap">
                      {rawPayInfo}
                    </pre>
                  </Field>
                )}
                <div className="flex items-center gap-2">
                  <Button asChild variant="outline" size="sm">
                    <Link to="/orders?order_type=2">
                      <IconListDetails className="size-4" />
                      {"在订单记录中查看测试单"}
                    </Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
