"use client"

import * as React from "react"
import { QRCodeSVG } from "qrcode.react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"
import { IconDownload, IconCopy } from "@tabler/icons-react"
import { toast } from "sonner"

interface QRCodeProps {
  value: string
  size?: number
  level?: "L" | "M" | "Q" | "H"
  bgColor?: string
  fgColor?: string
  includeMargin?: boolean
  className?: string
  /** 二维码白底容器的额外样式（默认内边距 p-3，可传 p-1 / p-2 收紧） */
  frameClassName?: string
  downloadable?: boolean
  copyable?: boolean
  /** 下载文件名，默认 qrcode.png */
  fileName?: string
  /** 传入后下载按钮改为带文字的普通按钮（否则为图标按钮） */
  downloadLabel?: string
}

function QRCode({
  value,
  size = 128,
  level = "M",
  bgColor = "transparent",
  fgColor = "currentColor",
  includeMargin = false,
  className,
  frameClassName,
  downloadable = false,
  copyable = false,
  fileName = "qrcode.png",
  downloadLabel,
}: QRCodeProps) {
  const svgRef = React.useRef<SVGSVGElement>(null)
  const { copyToClipboard } = useCopyToClipboard()

  const handleDownload = () => {
    const svg = svgRef.current
    if (!svg) return

    const canvas = document.createElement("canvas")
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const exportSize = size * 2
    canvas.width = exportSize
    canvas.height = exportSize

    const data = new XMLSerializer().serializeToString(svg)
    const img = new Image()
    img.onload = () => {
      ctx.fillStyle = bgColor === "transparent" ? "#ffffff" : bgColor
      ctx.fillRect(0, 0, exportSize, exportSize)
      ctx.drawImage(img, 0, 0, exportSize, exportSize)
      const a = document.createElement("a")
      a.download = fileName
      a.href = canvas.toDataURL("image/png")
      a.click()
    }
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data)
  }

  const handleCopy = async () => {
    const ok = await copyToClipboard(value)
    if (ok) toast.success("已复制到剪贴板")
    else toast.error("复制失败")
  }

  return (
    <div className={cn("inline-flex flex-col items-center gap-2", className)}>
      <div className={cn("rounded-md border bg-white text-foreground", frameClassName ?? "p-3")}>
        <QRCodeSVG
          ref={svgRef}
          value={value}
          size={size}
          level={level}
          bgColor={bgColor === "transparent" ? "#ffffff" : bgColor}
          fgColor={fgColor === "currentColor" ? "#000000" : fgColor}
          includeMargin={includeMargin}
        />
      </div>
      {(downloadable || copyable) && (
        <div className="flex w-full items-center justify-center gap-1">
          {downloadable &&
            (downloadLabel ? (
              <Button variant="secondary" size="sm" className="w-full gap-1.5" onClick={handleDownload}>
                <IconDownload className="size-3.5" />
                {downloadLabel}
              </Button>
            ) : (
              <Button variant="ghost" size="icon" className="size-7" onClick={handleDownload}>
                <IconDownload className="size-3.5" />
              </Button>
            ))}
          {copyable && (
            <Button variant="ghost" size="icon" className="size-7" onClick={handleCopy}>
              <IconCopy className="size-3.5" />
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

export { QRCode }
