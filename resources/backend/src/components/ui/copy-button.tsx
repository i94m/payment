"use client"

import * as React from "react"
import { IconCheck, IconCopy } from "@tabler/icons-react"
import { cn } from "@/lib/utils"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"

function CopyButton({
  value,
  className,
  onCopied,
  ...props
}: {
  value: string
  onCopied?: () => void
} & React.ComponentProps<"button">) {
  const { copyToClipboard, isCopied } = useCopyToClipboard()
  const timerRef = React.useRef<ReturnType<typeof setTimeout>>(undefined)

  const handleCopy = () => {
    copyToClipboard(value)
    onCopied?.()
  }

  React.useEffect(() => () => clearTimeout(timerRef.current), [])

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={cn(
        "inline-flex items-center justify-center shrink-0 rounded-md transition-colors",
        "text-muted-foreground hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className
      )}
      {...props}
    >
      {isCopied
        ? <IconCheck className="size-4 text-green-500" />
        : <IconCopy className="size-4" />}
    </button>
  )
}

function CopyableInput({
  value,
  className,
  onFocus,
  onClick,
  ...props
}: {
  value: string
} & Omit<React.ComponentProps<"input">, "type">) {
  const { copyToClipboard, isCopied } = useCopyToClipboard()

  return (
    <InputGroup className={className}>
      <InputGroupInput
        readOnly
        value={value}
        {...props}
        onFocus={(e) => {
          onFocus?.(e)
          e.currentTarget.select()
        }}
        onClick={(e) => {
          onClick?.(e)
          e.currentTarget.select()
        }}
      />
      <InputGroupAddon align="inline-end">
        <InputGroupButton
          aria-label="Copy"
          size="icon-xs"
          onClick={() => copyToClipboard(value)}
        >
          {isCopied ? <IconCheck className="text-green-500" /> : <IconCopy />}
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  )
}

function CopyableText({
  value,
  className,
}: {
  value: string
  className?: string
}) {
  const { copyToClipboard, isCopied } = useCopyToClipboard()

  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", className)}>
      {value}
      <button
        type="button"
        onClick={() => copyToClipboard(value)}
        className="inline-flex items-center justify-center shrink-0 rounded-md text-muted-foreground hover:text-foreground size-5 rounded p-0.5 hover:bg-muted"
      >
        {isCopied ? <IconCheck className="size-4 text-green-500" /> : <IconCopy className="size-4" />}
      </button>
    </span>
  )
}

export { CopyButton, CopyableInput, CopyableText }
