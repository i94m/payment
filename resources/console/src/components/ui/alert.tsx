import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Info, AlertTriangle, XCircle, CheckCircle2, X } from "lucide-react"

const alertVariants = cva(
  "relative flex items-start gap-3 rounded-lg border p-4 text-sm",
  {
    variants: {
      variant: {
        default: "border-border bg-background text-foreground",
        info: "border-blue-500/30 bg-blue-500/5 text-blue-700 dark:text-blue-400 [&>svg]:text-blue-600",
        destructive: "border-destructive/30 bg-destructive/5 text-destructive [&>svg]:text-destructive",
        warning: "border-yellow-500/30 bg-yellow-500/5 text-yellow-700 dark:text-yellow-400 [&>svg]:text-yellow-600",
        success: "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400 [&>svg]:text-emerald-600",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

const iconMap = {
  default: Info,
  info: Info,
  destructive: XCircle,
  warning: AlertTriangle,
  success: CheckCircle2,
}

interface AlertProps extends React.ComponentProps<"div">, VariantProps<typeof alertVariants> {
  onClose?: () => void
}

function Alert({ className, variant = "default", onClose, children, ...props }: AlertProps) {
  const Icon = iconMap[variant ?? "default"]
  return (
    <div
      data-slot="alert"
      role="alert"
      className={cn(alertVariants({ variant }), className)}
      {...props}
    >
      <Icon className="size-4 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">{children}</div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-sm p-0.5 opacity-60 hover:opacity-100 transition-opacity"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

function AlertTitle({ className, ...props }: React.ComponentProps<"h5">) {
  return (
    <h5
      data-slot="alert-title"
      className={cn("font-medium leading-none", className)}
      {...props}
    />
  )
}

function AlertDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn("text-sm opacity-80 mt-1", className)}
      {...props}
    />
  )
}

export { Alert, AlertTitle, AlertDescription }
