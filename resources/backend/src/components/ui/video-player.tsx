"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

interface VideoPlayerProps {
  src: string
  width?: number | string
  height?: number | string
  autoPlay?: boolean
  controls?: boolean
  className?: string
  onLoadedMetadata?: (meta: { duration: number; videoWidth: number; videoHeight: number }) => void
}

export function VideoPlayer({
  src,
  width,
  height,
  autoPlay = false,
  controls = true,
  className,
  onLoadedMetadata,
}: VideoPlayerProps) {
  const ref = React.useRef<HTMLVideoElement>(null)

  const handleLoadedMetadata = React.useCallback(() => {
    const video = ref.current
    if (!video || !onLoadedMetadata) return
    onLoadedMetadata({
      duration: video.duration,
      videoWidth: video.videoWidth,
      videoHeight: video.videoHeight,
    })
  }, [onLoadedMetadata])

  return (
    <video
      ref={ref}
      src={src}
      width={width}
      height={height}
      autoPlay={autoPlay}
      controls={controls}
      onLoadedMetadata={handleLoadedMetadata}
      className={cn("rounded-md bg-black", className)}
      style={{ maxWidth: "100%" }}
    >
      您的浏览器不支持视频播放
    </video>
  )
}
