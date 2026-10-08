import * as React from "react"
import { cn } from "@/lib/utils"

interface WatermarkProps {
  content: string
  fontSize?: number
  color?: string
  rotate?: number
  gap?: number
  className?: string
  children?: React.ReactNode
}

function Watermark({
  content,
  fontSize = 14,
  color = "rgba(128,128,128,0.12)",
  rotate = -22,
  gap = 100,
  className,
  children,
}: WatermarkProps) {
  const bgUrl = React.useMemo(() => {
    const canvas = document.createElement("canvas")
    const ctx = canvas.getContext("2d")
    if (!ctx) return ""

    const text = content
    ctx.font = `${fontSize}px sans-serif`
    const metrics = ctx.measureText(text)
    const textWidth = metrics.width
    const textHeight = fontSize * 1.2

    const canvasWidth = textWidth + gap
    const canvasHeight = textHeight + gap
    canvas.width = canvasWidth
    canvas.height = canvasHeight

    ctx.font = `${fontSize}px sans-serif`
    ctx.fillStyle = color
    ctx.textBaseline = "middle"
    ctx.translate(canvasWidth / 2, canvasHeight / 2)
    ctx.rotate((rotate * Math.PI) / 180)
    ctx.fillText(text, -textWidth / 2, 0)

    return canvas.toDataURL()
  }, [content, fontSize, color, rotate, gap])

  return (
    <div className={cn("relative", className)}>
      {children}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage: `url(${bgUrl})`,
          backgroundRepeat: "repeat",
        }}
      />
    </div>
  )
}

export { Watermark }
