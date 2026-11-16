import * as React from "react"
import { useForm, type FieldValues } from "react-hook-form"
import { toast } from "sonner"
import { zodResolver } from "@hookform/resolvers/zod"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldGroup } from "@/components/ui/field"
import { deriveRequiredFields } from "@/lib/form-utils"
import { getErrorMessage } from "@/lib/error"
import { FormFieldRenderer } from "./form-field-renderer"
import { PanelError } from "./list-error"
import type { FormDialogConfig } from "./types"

interface FormDialogInnerProps extends FormDialogConfig<FieldValues> {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingId: number | null
  onSuccess?: () => void
}

export function FormDialog({
  open,
  onOpenChange,
  editingId,
  schema,
  fields,
  defaultValues,
  onSubmit,
  fetchDetail,
  createTitle,
  editTitle,
  width,
  onSuccess,
}: FormDialogInnerProps) {
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [detailError, setDetailError] = React.useState<unknown>(null)
  const [saving, setSaving] = React.useState(false)

  const form = useForm<FieldValues>({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    resolver: zodResolver(schema as any),
    defaultValues: defaultValues(),
  })

  const formData = form.watch()

  // 必填星号：字段级 required 覆写优先，否则从 schema 推导（z.string().min(1) 一类才算必填）
  const requiredMap = React.useMemo(() => deriveRequiredFields(schema), [schema])

  const loadDetail = React.useCallback(() => {
    if (!editingId || !fetchDetail) return
    setDetailLoading(true)
    setDetailError(null)
    fetchDetail(editingId)
      .then((data) => {
        form.reset({ ...defaultValues(), ...data })
      })
      .catch((err) => setDetailError(err))
      .finally(() => setDetailLoading(false))
    // defaultValues/form 由父组件在 open 时提供，避免每次渲染重复拉详情
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingId, fetchDetail])

  React.useEffect(() => {
    if (!open) return
    if (editingId && fetchDetail) {
      loadDetail()
    } else {
      setDetailError(null)
      form.reset(defaultValues())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editingId])

  const handleSubmit = form.handleSubmit(async (values) => {
    setSaving(true)
    try {
      await onSubmit(editingId, values)
      onOpenChange(false)
      onSuccess?.()
    } catch (err) {
      // 提交失败保持弹窗打开并保留已填内容，同时把后端真实原因告知用户
      toast.error(getErrorMessage(err, "保存失败，请稍后重试"))
    } finally {
      setSaving(false)
    }
  })

  const title = editingId ? (editTitle ?? "Edit") : (createTitle ?? "Create")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={width ?? "sm:max-w-3xl max-h-[90vh] overflow-y-auto"}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        {detailLoading ? (
          <div className="space-y-4 py-4">
            <div className="h-8 bg-muted rounded animate-pulse" />
            <div className="h-8 bg-muted rounded animate-pulse w-3/4" />
            <div className="h-20 bg-muted rounded animate-pulse" />
          </div>
        ) : detailError ? (
          <>
            <PanelError error={detailError} onRetry={loadDetail} />
            <DialogFooter className="mt-6">
              <Button variant="outline" type="button" onClick={() => onOpenChange(false)}>
                {"关闭"}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <FieldGroup>
              <div className="grid gap-4 sm:grid-cols-2">
                {fields.map((config) => (
                  <div
                    key={config.name}
                    className={config.span === "full" ? "sm:col-span-2" : undefined}
                  >
                    <FormFieldRenderer
                      config={
                        // 显式 required 优先，否则用 schema 推导值
                        config.required == null
                          ? { ...config, required: requiredMap[config.name] }
                          : config
                      }
                      control={form.control}
                      formData={formData as Record<string, unknown>}
                      disabled={saving}
                    />
                  </div>
                ))}
              </div>
            </FieldGroup>
            <DialogFooter className="mt-6">
              <Button
                variant="outline"
                type="button"
                onClick={() => onOpenChange(false)}
              >
                {"取消"}
              </Button>
              <Button type="submit" disabled={saving}>
                {saving ? "加载中..." : "保存"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
