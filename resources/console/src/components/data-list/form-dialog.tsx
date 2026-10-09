import * as React from "react"
import { useForm, type FieldValues } from "react-hook-form"
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
import { FormFieldRenderer } from "./form-field-renderer"
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
  const [saving, setSaving] = React.useState(false)

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const form = useForm<FieldValues>({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    resolver: zodResolver(schema as any),
    defaultValues: defaultValues(),
  })

  const formData = form.watch()

  React.useEffect(() => {
    if (!open) return

    const defaults = defaultValues()
    if (editingId && fetchDetail) {
      setDetailLoading(true)
      fetchDetail(editingId)
        .then((data) => {
          form.reset({ ...defaults, ...data })
        })
        .finally(() => setDetailLoading(false))
    } else {
      form.reset(defaults)
    }
  }, [open, editingId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = form.handleSubmit(async (values) => {
    setSaving(true)
    try {
      await onSubmit(editingId, values)
      onOpenChange(false)
      onSuccess?.()
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
                      config={config}
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
