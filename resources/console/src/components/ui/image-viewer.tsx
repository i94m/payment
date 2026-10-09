"use client"

import * as React from "react"
import { cn } from "@/lib/utils"
import { getFileUrl } from "@/lib/storage"
import { IconX, IconChevronLeft, IconChevronRight, IconZoomIn, IconZoomOut, IconRotateClockwise, IconRotate2 } from "@tabler/icons-react"

/* ---- ImageViewer: single or multiple images with preview ---- */

type ImageViewerSize = "sm" | "md" | "lg"

interface ImageViewerProps {
  src?: string | string[]
  width?: number | string
  height?: number | string
  className?: string
  max?: number
  size?: ImageViewerSize
}

const sizeMap: Record<ImageViewerSize, { width: number; height: number }> = {
  sm: { width: 32, height: 32 },
  md: { width: 64, height: 64 },
  lg: { width: 96, height: 96 },
}

function ImageViewer({
  src,
  width,
  height,
  className,
  max,
  size = "md",
}: ImageViewerProps) {
  const resolved = sizeMap[size]
  const w = width ?? resolved.width
  const h = height ?? resolved.height
  const list = React.useMemo(() => {
    if (!src) return []
    const arr = Array.isArray(src) ? src : [src]
    return arr.filter(Boolean).map((s) => getFileUrl(s))
  }, [src])

  if (list.length === 0) return null

  const display = max && list.length > max ? list.slice(0, max) : list
  const overflow = max && list.length > max ? list.length - max : 0

  return (
    <div className={cn("flex flex-wrap gap-1", className)}>
      {display.map((url, i) => (
        <ImageThumb
          key={i}
          src={url}
          width={w}
          height={h}
          onClick={() => openPreview(list, i)}
        />
      ))}
      {overflow > 0 && (
        <ImageThumb
          src={display[display.length - 1]}
          width={w}
          height={h}
          onClick={() => openPreview(list, display.length)}
          overlay={`+${overflow}`}
        />
      )}
    </div>
  )
}

/* ---- ImageThumb ---- */

interface ImageThumbProps {
  src: string
  width?: number | string
  height?: number | string
  onClick?: () => void
  overlay?: string
}

function ImageThumb({ src, width = 64, height = 64, onClick, overlay }: ImageThumbProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "relative group cursor-pointer rounded-md overflow-hidden border border-border bg-muted shrink-0",
        "hover:ring-2 hover:ring-primary/50 transition-all",
      )}
      style={{ width, height }}
    >
      <img
        src={src}
        alt=""
        className="size-full object-cover"
        loading="lazy"
      />
      <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors flex items-center justify-center">
        <IconZoomIn className="size-4 text-white opacity-0 group-hover:opacity-100 transition-opacity drop-shadow" />
      </div>
      {overlay && (
        <div className="absolute inset-0 bg-black/50 flex items-center justify-center text-white text-sm font-medium">
          {overlay}
        </div>
      )}
    </div>
  )
}

/* ---- Preview Modal (singleton) ---- */

let _openPreview: ((urls: string[], index: number) => void) | null = null

function openPreview(urls: string[], index: number) {
  _openPreview?.(urls, index)
}

function ImagePreviewPortal() {
  const [state, setState] = React.useState<{
    urls: string[]
    index: number
  } | null>(null)

  _openPreview = (urls, index) => setState({ urls, index })

  const close = React.useCallback(() => setState(null), [])

  if (!state) return null

  return <ImagePreviewModal urls={state.urls} initial={state.index} onClose={close} />
}

/* ---- Preview Modal ---- */

interface ImagePreviewModalProps {
  urls: string[]
  initial: number
  onClose: () => void
}

function ImagePreviewModal({ urls, initial, onClose }: ImagePreviewModalProps) {
  const [index, setIndex] = React.useState(initial)
  const [zoom, setZoom] = React.useState(1)
  const [rotate, setRotate] = React.useState(0)

  const hasPrev = index > 0
  const hasNext = index < urls.length - 1
  const showNav = urls.length > 1

  const go = React.useCallback(
    (delta: number) => {
      setIndex((i) => Math.max(0, Math.min(urls.length - 1, i + delta)))
      setZoom(1)
      setRotate(0)
    },
    [urls.length],
  )

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      if (e.key === "ArrowLeft") go(-1)
      if (e.key === "ArrowRight") go(1)
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [onClose, go])

  React.useEffect(() => {
    setZoom(1)
    setRotate(0)
  }, [index])

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center"
    >
      {/* Close */}
      <button
        onClick={onClose}
        className="absolute top-4 right-4 z-10 text-white/70 hover:text-white transition-colors"
      >
        <IconX className="size-6" />
      </button>

      {/* Counter */}
      {showNav && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 text-white/70 text-sm">
          {index + 1} / {urls.length}
        </div>
      )}

      {/* Toolbar */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1 bg-black/50 rounded-lg px-2 py-1 z-10">
        <ToolbarBtn
          onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
          icon={<IconZoomOut className="size-4" />}
        />
        <span className="text-white/70 text-xs w-10 text-center">{Math.round(zoom * 100)}%</span>
        <ToolbarBtn
          onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
          icon={<IconZoomIn className="size-4" />}
        />
        <div className="w-px h-4 bg-white/20 mx-1" />
        <ToolbarBtn
          onClick={() => setRotate((r) => r - 90)}
          icon={<IconRotate2 className="size-4" />}
        />
        <ToolbarBtn
          onClick={() => setRotate((r) => r + 90)}
          icon={<IconRotateClockwise className="size-4" />}
        />
      </div>

      {/* Prev */}
      {showNav && hasPrev && (
        <button
          onClick={() => go(-1)}
          className="absolute left-4 top-1/2 -translate-y-1/2 z-10 text-white/70 hover:text-white transition-colors bg-black/30 rounded-full p-2"
        >
          <IconChevronLeft className="size-6" />
        </button>
      )}

      {/* Next */}
      {showNav && hasNext && (
        <button
          onClick={() => go(1)}
          className="absolute right-4 top-1/2 -translate-y-1/2 z-10 text-white/70 hover:text-white transition-colors bg-black/30 rounded-full p-2"
        >
          <IconChevronRight className="size-6" />
        </button>
      )}

      {/* Image */}
      <img
        key={urls[index]}
        src={urls[index]}
        alt=""
        className="max-w-[90vw] max-h-[85vh] object-contain transition-transform duration-200"
        style={{
          transform: `scale(${zoom}) rotate(${rotate}deg)`,
        }}
      />
    </div>
  )
}

function ToolbarBtn({ onClick, icon }: { onClick: () => void; icon: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="text-white/70 hover:text-white transition-colors p-1"
    >
      {icon}
    </button>
  )
}

export { ImageViewer, ImagePreviewPortal, openPreview }
