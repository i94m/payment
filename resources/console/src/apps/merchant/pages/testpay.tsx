"use client"

import * as React from "react"
import { testPayApi } from "@/api"
import type { TestPayResult, TestPayType } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Field, FieldDescription, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { PayIcon } from "@/components/pay-icon"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { numStr } from "@/lib/form-schema"
import { getErrorMessage } from "@/lib/error"
import { IconFlask, IconExternalLink } from "@tabler/icons-react"
import { toast } from "sonner"

// 金额范围与后端 /testpay/submit 口径一致
const testSchema = z.object({
  type: z.string().min(1, "请选择支付方式").default(""),
  name: z.string().default("支付测试"),
  amount: numStr("支付金额", { min: 0.01, max: 1000 }),
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
        toast.success("支付页已打开")
      } else {
        toast.success("支付信息已生成")
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
            {"发起一笔真实支付，验证支付通道是否可用。"}
          </p>
        </div>

        <div className="px-4 lg:px-6 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{"发起支付"}</CardTitle>
              <CardDescription>
                {"支付页会在新窗口打开，支付完成后自动返回。"}
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
                        key={typesLoading ? "loading" : "ready"}
                        value={field.value}
                        onValueChange={field.onChange}
                        disabled={typesLoading || types.length === 0}
                        items={types.map((t) => ({ value: t.code, label: t.name }))}
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
                      <FieldLabel htmlFor={field.name}>{"支付金额"}<RequiredMark /></FieldLabel>
                      <Input
                        id={field.name}
                        type="number" min="0.01" max="1000" step="0.01"
                        value={field.value ?? ""}
                        onChange={(e) => field.onChange(e.target.value)}
                        aria-invalid={fieldState.invalid || undefined}
                      />
                      <FieldDescription>{"0.01 ~ 1000 元。"}</FieldDescription>
                      {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                    </Field>
                  )}
                />
                <Button type="submit" disabled={submitting || types.length === 0}>
                  {submitting ? "发起中…" : "发起支付"}
                </Button>
              </form>
            </CardContent>
          </Card>

          {result && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{"支付信息已生成"}</CardTitle>
                <CardDescription>{`支付动作：${result.pay_type}`}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {payUrl && (
                  <Button
                    variant="outline"
                    nativeButton={false}
                    render={<a href={payUrl} target="_blank" rel="noreferrer" />}
                  >
                    <IconExternalLink className="size-4" />
                    {"重新打开支付页"}
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
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
