"use client"

import * as React from "react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { IconUpload, IconX, IconFile, IconPhoto, IconVideo, IconLoader2 } from "@tabler/icons-react"
import apiClient from "@/lib/api-client"
import { getFileUrl } from "@/lib/storage"
import { Progress } from "@/components/ui/progress"
import { VideoPlayer } from "@/components/ui/video-player"
import { openPreview } from "@/components/ui/image-viewer"
import {
  Attachment,
  AttachmentMedia,
  AttachmentContent,
  AttachmentTitle,
  AttachmentDescription,
  AttachmentActions,
  AttachmentAction,
} from "@/components/ui/attachment"

interface UploaderProps {
  value?: string | string[]
  onChange?: (value: string | string[]) => void
  policy?: string
  multiple?: boolean
  accept?: string
  maxSize?: number
  maxCount?: number
  disabled?: boolean
  className?: string
  draggable?: boolean
}

interface UploadFile {
  uid: string
  name: string
  url: string
  key?: string
  status: "uploading" | "done" | "error"
  percent: number
}

interface StsConfig {
  provider: string
  prefix: string
  upload_url: string
  url: string
  payload: Record<string, string | number>
  extensions: string[]
  max_file_size: number
  maximum: number
  success_action_status: number
  withCredentials?: boolean
}

type AttachState = "uploading" | "error" | "done"

function uid() {
  return Math.random().toString(36).slice(2)
}

function randomKey(prefix: string, file: File): string {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789"
  let str = ""
  for (let i = 0; i < 32; i++) str += chars[Math.floor(Math.random() * chars.length)]
  const ext = file.name.split(".").pop() || "bin"
  return prefix + str + "." + ext
}

function fileListFromValue(value?: string | string[]): UploadFile[] {
  if (!value) return []
  const keys = Array.isArray(value) ? value : [value]
  return keys.filter(Boolean).map((key) => ({
    uid: uid(),
    name: key.split("/").pop() || key,
    url: getFileUrl(key),
    key,
    status: "done" as const,
    percent: 100,
  }))
}

function valuesEqual(a?: string | string[], b?: string | string[]): boolean {
  if (a === b) return true
  if (!a && !b) return true
  if (!a || !b) return false
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false
    return a.every((v, i) => v === b[i])
  }
  return false
}

function extOf(name: string): string {
  const ext = name.split(".").pop()
  return ext ? ext.toUpperCase() : "FILE"
}

function Uploader({
  value,
  onChange,
  policy = "image",
  multiple = false,
  accept,
  maxSize,
  maxCount,
  disabled = false,
  className,
  draggable = false,
}: UploaderProps) {
  const inputRef = React.useRef<HTMLInputElement>(null)
  const [fileList, setFileList] = React.useState<UploadFile[]>(() => fileListFromValue(value))
  const [dragOver, setDragOver] = React.useState(false)
  const [stsConfig, setStsConfig] = React.useState<StsConfig | null>(null)
  const [stsError, setStsError] = React.useState("")
  const lastEmittedValue = React.useRef(value)
  const fileListRef = React.useRef(fileList)
  fileListRef.current = fileList

  const doneCount = React.useMemo(
    () => fileList.filter((f) => f.status === "done").length,
    [fileList],
  )

  const resolvedMaxCount = React.useMemo(() => {
    if (maxCount != null) return maxCount
    if (stsConfig?.maximum) return stsConfig.maximum
    return multiple ? 9 : 1
  }, [maxCount, stsConfig?.maximum, multiple])

  const resolvedMaxSize = React.useMemo(() => {
    if (maxSize != null) return maxSize
    if (stsConfig?.max_file_size) return stsConfig.max_file_size
    return 10 * 1024 * 1024
  }, [maxSize, stsConfig?.max_file_size])

  const canUpload = React.useMemo(
    () => !disabled && (!multiple || doneCount < resolvedMaxCount),
    [disabled, multiple, doneCount, resolvedMaxCount],
  )

  const isImage = React.useMemo(() => {
    if (accept) return accept.includes("image")
    if (stsConfig?.extensions.length) {
      return stsConfig.extensions.some((e) => [".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp"].includes(e))
    }
    return true
  }, [accept, stsConfig?.extensions])

  const isVideo = React.useMemo(() => {
    if (accept) return accept.includes("video")
    if (stsConfig?.extensions.length) {
      return stsConfig.extensions.some((e) => [".mp4", ".webm", ".ogg", ".mov", ".avi"].includes(e))
    }
    return false
  }, [accept, stsConfig?.extensions])

  React.useEffect(() => {
    if (valuesEqual(value, lastEmittedValue.current)) return
    const next = fileListFromValue(value)
    lastEmittedValue.current = value
    fileListRef.current = next
    setFileList(next)
  }, [value])

  React.useEffect(() => {
    apiClient
      .get<any>("/upload/sts", { params: { policy } })
      .then((res) => {
        const data = res.data ?? res
        if (data.provider === "local") {
          data.withCredentials = true
        }
        setStsConfig(data)
      })
      .catch((err) => {
        setStsError(err.message || "获取上传配置失败")
      })
  }, [policy])

  const emitChange = React.useCallback(
    (list: UploadFile[]) => {
      const doneKeys = list.filter((f) => f.status === "done").map((f) => f.key || f.url).filter(Boolean)
      const result = multiple ? doneKeys : doneKeys[0] || ""
      lastEmittedValue.current = result
      onChange?.(result)
    },
    [multiple, onChange],
  )

  const uploadFile = React.useCallback(
    (file: File) => {
      if (!stsConfig) return

      if (file.size > resolvedMaxSize) {
        alert(`文件大小不能超过 ${(resolvedMaxSize / 1024 / 1024).toFixed(0)}MB`)
        return
      }

      const ext = file.name.split(".").pop()?.toLowerCase() || ""
      if (stsConfig.extensions.length && !stsConfig.extensions.includes(`.${ext}`)) {
        alert(`不支持的文件类型: .${ext}`)
        return
      }

      const key = randomKey(stsConfig.prefix, file)
      const fileUid = uid()

      const entry: UploadFile = {
        uid: fileUid,
        name: file.name,
        url: getFileUrl(key),
        key,
        status: "uploading",
        percent: 0,
      }

      const prev = fileListRef.current
      const next = !multiple ? [entry] : [...prev, entry]
      fileListRef.current = next
      setFileList(next)

      const formData = new FormData()
      Object.entries(stsConfig.payload).forEach(([k, v]) => {
        formData.append(k, String(v))
      })
      formData.append("key", key)
      formData.append("file", file)

      const xhr = new XMLHttpRequest()
      xhr.withCredentials = !!stsConfig.withCredentials

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100)
          setFileList((prev) =>
            prev.map((f) => (f.uid === fileUid ? { ...f, percent: pct } : f)),
          )
        }
      }

      xhr.onload = () => {
        if (xhr.status === stsConfig.success_action_status || xhr.status === 200) {
          setFileList((prev) =>
            prev.map((f) => (f.uid === fileUid ? { ...f, status: "done", percent: 100 } : f)),
          )
          const updated = fileListRef.current.map((f) =>
            f.uid === fileUid ? { ...f, status: "done" as const, percent: 100 } : f,
          )
          fileListRef.current = updated
          emitChange(updated)
        } else {
          setFileList((prev) =>
            prev.map((f) => (f.uid === fileUid ? { ...f, status: "error" } : f)),
          )
        }
      }

      xhr.onerror = () => {
        setFileList((prev) =>
          prev.map((f) => (f.uid === fileUid ? { ...f, status: "error" } : f)),
        )
      }

      xhr.open("POST", stsConfig.upload_url || stsConfig.url)
      xhr.send(formData)
    },
    [stsConfig, resolvedMaxSize, multiple, emitChange],
  )

  const handleFiles = React.useCallback(
    (files: FileList | null) => {
      if (!files) return
      Array.from(files).forEach(uploadFile)
    },
    [uploadFile],
  )

  const handleRemove = React.useCallback(
    (index: number) => {
      const next = fileListRef.current.filter((_, i) => i !== index)
      fileListRef.current = next
      setFileList(next)
      emitChange(next)
    },
    [emitChange],
  )

  const countLabel = multiple && resolvedMaxCount > 1
    ? ` ${doneCount}/${resolvedMaxCount}`
    : ""

  const tip = React.useMemo(() => {
    if (!stsConfig) return ""
    const exts = stsConfig.extensions.join(", ")
    const sizeStr = resolvedMaxSize >= 1024 * 1024
      ? `${(resolvedMaxSize / 1024 / 1024).toFixed(0)}MB`
      : `${(resolvedMaxSize / 1024).toFixed(0)}KB`
    return `支持 ${exts} 格式且大小不超过 ${sizeStr} 的${isImage ? "图片" : isVideo ? "视频" : "文件"}`
  }, [stsConfig, resolvedMaxSize, isImage, isVideo])

  const doneUrls = React.useMemo(
    () => fileList.filter((f) => f.status === "done").map((f) => f.url),
    [fileList],
  )

  const Icon = isImage ? IconPhoto : isVideo ? IconVideo : IconFile

  const stateOf = React.useCallback(
    (f: UploadFile): AttachState => (f.status === "uploading" ? "uploading" : f.status === "error" ? "error" : "done"),
    [],
  )

  if (stsError) {
    return (
      <div className={cn("text-sm text-destructive", className)}>
        {stsError}
      </div>
    )
  }

  if (!stsConfig) {
    return (
      <div className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}>
        <IconLoader2 className="size-4 animate-spin" />
        加载上传配置...
      </div>
    )
  }

  return (
    <div className={cn("space-y-2", className)}>
      {(isImage || isVideo) && fileList.length > 0 && (
        <div className={cn(isImage ? "flex flex-wrap gap-2" : "space-y-2")}>
          {fileList.map((f, i) => {
            const state = stateOf(f)
            return (
              <Attachment
                key={f.uid}
                state={state}
                orientation="vertical"
                className={cn(isImage ? "w-24" : "w-full max-w-sm")}
              >
                <AttachmentMedia
                  variant={isImage && f.status === "done" ? "image" : "icon"}
                  className={cn(
                    isVideo && "aspect-video rounded-md p-0",
                    f.status === "uploading" && "text-muted-foreground",
                  )}
                >
                  {isImage && f.status === "done" && (
                    <img
                      src={f.url}
                      alt={f.name}
                      className="size-full cursor-pointer object-cover"
                      loading="lazy"
                      onClick={() => openPreview(doneUrls, doneUrls.indexOf(f.url))}
                    />
                  )}
                  {isVideo && f.status === "done" && (
                    <VideoPlayer src={f.url} controls className="size-full rounded-md" />
                  )}
                  {f.status === "uploading" && (
                    <div className="flex flex-col items-center justify-center gap-1">
                      <IconLoader2 className="size-4 animate-spin" />
                      <span className="text-[10px] tabular-nums">{f.percent}%</span>
                    </div>
                  )}
                  {f.status === "error" && <IconFile className="size-4 text-destructive" />}
                </AttachmentMedia>
                {isVideo && (
                  <AttachmentContent>
                    <AttachmentTitle className="text-xs">{f.name}</AttachmentTitle>
                    <AttachmentDescription>
                      {f.status === "uploading"
                        ? `上传中 · ${f.percent}%`
                        : f.status === "error"
                          ? "上传失败，请重试"
                          : "视频"}
                    </AttachmentDescription>
                    {f.status === "uploading" && (
                      <Progress value={f.percent} className="mt-1.5 h-1" />
                    )}
                  </AttachmentContent>
                )}
                <AttachmentActions className="opacity-0 transition-opacity group-hover/attachment:opacity-100 focus-within:opacity-100">
                  {f.status !== "uploading" && (
                    <AttachmentAction
                      aria-label={`移除 ${f.name}`}
                      className="bg-black/50 text-white hover:bg-black/70 hover:text-white"
                      onClick={() => handleRemove(i)}
                    >
                      <IconX />
                    </AttachmentAction>
                  )}
                </AttachmentActions>
              </Attachment>
            )
          })}
        </div>
      )}

      {!isImage && !isVideo && fileList.length > 0 && (
        <div className="space-y-2">
          {fileList.map((f, i) => {
            const state = stateOf(f)
            return (
              <Attachment key={f.uid} state={state} orientation="horizontal" size="sm">
                <AttachmentMedia variant="icon">
                  {f.status === "error" ? <IconFile className="text-destructive" /> : <Icon />}
                </AttachmentMedia>
                <AttachmentContent>
                  <AttachmentTitle>{f.name}</AttachmentTitle>
                  <AttachmentDescription>
                    {f.status === "uploading"
                      ? `上传中 · ${f.percent}%`
                      : f.status === "error"
                        ? "上传失败，请重试"
                        : extOf(f.name)}
                  </AttachmentDescription>
                  {f.status === "uploading" && (
                    <Progress value={f.percent} className="mt-1.5 h-1" />
                  )}
                </AttachmentContent>
                <AttachmentActions>
                  {f.status !== "uploading" && (
                    <AttachmentAction
                      aria-label={`移除 ${f.name}`}
                      onClick={() => handleRemove(i)}
                    >
                      <IconX />
                    </AttachmentAction>
                  )}
                </AttachmentActions>
              </Attachment>
            )
          })}
        </div>
      )}

      {canUpload && (
        draggable ? (
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              handleFiles(e.dataTransfer.files)
            }}
            onClick={() => inputRef.current?.click()}
            className={cn(
              "flex items-center justify-center rounded-md border-2 border-dashed transition-colors cursor-pointer h-20",
              dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/50",
            )}
          >
            <div className="flex flex-col items-center gap-1 text-muted-foreground">
              <IconUpload className="size-5" />
              <span className="text-xs">点击或拖拽上传{countLabel}</span>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => inputRef.current?.click()}
            >
              <Icon className="size-4" />
              上传{isImage ? "图片" : isVideo ? "视频" : "文件"}{countLabel}
            </Button>
            {tip && (
              <p className="text-xs text-muted-foreground">{tip}</p>
            )}
          </div>
        )
      )}

      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept={accept || stsConfig.extensions.join(",")}
        multiple={multiple}
        disabled={!canUpload}
        onChange={(e) => {
          handleFiles(e.target.files)
          e.target.value = ""
        }}
      />
    </div>
  )
}

export { Uploader }
