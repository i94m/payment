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
  downloadable?: boolean
  copyable?: boolean
}

function QRCode({
  value,
  size = 128,
  level = "M",
  bgColor = "transparent",
  fgColor = "currentColor",
  includeMargin = false,
  className,
  downloadable = false,
  copyable = false,
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
      a.download = "qrcode.png"
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
      <div className="rounded-md border bg-white p-3 text-foreground">
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
        <div className="flex items-center gap-1">
          {downloadable && (
            <Button variant="ghost" size="icon" className="size-7" onClick={handleDownload}>
              <IconDownload className="size-3.5" />
            </Button>
          )}
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
