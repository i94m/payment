import * as React from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface DeleteConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: string
  message: string
  variant?: "single" | "batch"
  count?: number
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => Promise<void>
}

export function DeleteConfirmDialog({
  open,
  onOpenChange,
  title,
  message,
  variant = "single",
  count,
  confirmLabel,
  cancelLabel,
  onConfirm,
}: DeleteConfirmDialogProps) {
  const [loading, setLoading] = React.useState(false)

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await onConfirm()
      onOpenChange(false)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title ?? (variant === "batch" ? "Batch Delete" : "Delete")}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {variant === "batch" && count != null
            ? `${message} (${count} items)`
            : message}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {cancelLabel ?? "Cancel"}
          </Button>
          <Button variant="destructive" onClick={handleConfirm} disabled={loading}>
            {confirmLabel ?? "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
