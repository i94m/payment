"use client"

import * as React from "react"
import type { Editor } from "@tiptap/react"
import { TextSelection } from "@tiptap/pm/state"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import {
  formatFileSize,
  getStsConfig,
  isImageFile,
  uploadFile,
  type StsConfig,
  type UploadResult,
} from "@/lib/upload"

const DEFAULT_IMAGE_ACCEPT = ".jpg,.jpeg,.png,.webp,.avif"

interface StsMeta {
  imageAccept: string
  fileAccept: string
  fileMaxSize: number
}

export interface EditorInputRefs {
  imageInputRef: React.RefObject<HTMLInputElement | null>
  fileInputRef: React.RefObject<HTMLInputElement | null>
}

function toAccept(config: StsConfig | null, fallback: string): string {
  if (!config?.extensions?.length) return fallback
  return config.extensions.join(",")
}

/**
 * 把光标移到 pos 附近。
 *
 * 图片插入后 ProseMirror 选中的是节点本身（NodeSelection），此时直接
 * `setTextSelection(to)` 可能落在块级边界上触发 "endpoint not pointing into a
 * node with inline content" 告警；`TextSelection.near` 会就近吸附到合法文本位置。
 */
function placeCursorNear(editor: Editor, pos: number) {
  const { state, view } = editor
  const clamped = Math.min(Math.max(pos, 0), state.doc.content.size)
  const selection = TextSelection.near(state.doc.resolve(clamped))
  if (selection && !state.selection.eq(selection)) {
    view.dispatch(state.tr.setSelection(selection))
  }
}

/** 上传单个文件，返回结果；失败已 toast，返回 null */
async function uploadOne(file: File): Promise<UploadResult | null> {
  // 图片走 image 策略，其余走 file 策略
  const policy = isImageFile(file) ? "image" : "file"
  const toastId = toast.loading(`正在上传 ${file.name}`, { description: "0%" })
  try {
    const result = await uploadFile(file, {
      policy,
      onProgress: (percent) => {
        toast.loading(`正在上传 ${file.name}`, { id: toastId, description: `${percent}%` })
      },
    })
    toast.success(`上传完成：${file.name}`, { id: toastId })
    return result
  } catch (err) {
    toast.error(getErrorMessage(err, `上传失败：${file.name}`), { id: toastId })
    return null
  }
}

/**
 * 上传并插入内容：图片插成图片节点，其他文件插成文件名链接。
 *
 * 按钮、拖拽、粘贴三条入口共用；放在模块级是为了让 FileHandler 插件回调
 * 不依赖组件闭包/ref（React Compiler 对渲染期读 ref 会告警）。
 *
 * @param pos 拖拽落点（ProseMirror 文档位置），不传则插入当前光标处
 */
export async function uploadFilesIntoEditor(editor: Editor, files: File[], pos?: number) {
  for (const file of files) {
    const result = await uploadOne(file)
    if (!result) continue

    if (isImageFile(file)) {
      const node = { type: "image", attrs: { src: result.url, alt: file.name } }
      if (typeof pos === "number") {
        editor.chain().focus().insertContentAt(pos, node).run()
      } else {
        editor.chain().focus().setImage({ src: result.url, alt: file.name }).run()
      }
      // 插入图片后 ProseMirror 会选中该节点，紧接着插入其他内容会把图片替换掉；
      // 这里把光标移到图片之后，符合"继续写正文"的直觉
      placeCursorNear(editor, editor.state.selection.to)
      continue
    }

    // 普通文件：插入一个带文件名文字的链接
    const link = {
      type: "text",
      text: file.name,
      marks: [{ type: "link", attrs: { href: result.url } }],
    }
    if (typeof pos === "number") {
      editor.chain().focus().insertContentAt(pos, link).run()
    } else {
      editor.chain().focus().insertContent(link).run()
    }
  }
}

/** FileHandler 拖拽回调 */
export function handleEditorDrop(editor: Editor, files: File[], pos: number) {
  void uploadFilesIntoEditor(editor, files, pos)
}

/** FileHandler 粘贴回调 */
export function handleEditorPaste(editor: Editor, files: File[]) {
  void uploadFilesIntoEditor(editor, files)
}

/**
 * 富文本上传能力：图片走 image 策略、其余文件走 file 策略，
 * 三条入口（按钮 / 拖拽 / 粘贴）共用同一套上传与插入逻辑。
 *
 * 隐藏 input 的 ref 由调用方持有（组件内创建），这里只负责触发与元信息。
 */
export function useEditorUpload(editor: Editor | null, refs: EditorInputRefs) {
  const { imageInputRef, fileInputRef } = refs
  const [meta, setMeta] = React.useState<StsMeta>({
    imageAccept: DEFAULT_IMAGE_ACCEPT,
    fileAccept: "",
    fileMaxSize: 0,
  })

  // 策略元信息用于 accept 与提示文案（失败不阻塞，后端仍会二次校验）
  React.useEffect(() => {
    let cancelled = false
    Promise.allSettled([getStsConfig("image"), getStsConfig("file")]).then(([image, file]) => {
      if (cancelled) return
      setMeta({
        imageAccept: toAccept(image.status === "fulfilled" ? image.value : null, DEFAULT_IMAGE_ACCEPT),
        fileAccept: toAccept(file.status === "fulfilled" ? file.value : null, ""),
        fileMaxSize: file.status === "fulfilled" ? file.value.max_file_size : 0,
      })
    })
    return () => {
      cancelled = true
    }
  }, [])

  const openImagePicker = React.useCallback(() => imageInputRef.current?.click(), [imageInputRef])
  const openFilePicker = React.useCallback(() => fileInputRef.current?.click(), [fileInputRef])

  const onInputChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? [])
      event.target.value = ""
      if (editor && files.length) void uploadFilesIntoEditor(editor, files)
    },
    [editor],
  )

  return {
    imageAccept: meta.imageAccept,
    fileAccept: meta.fileAccept,
    fileMaxSizeLabel: meta.fileMaxSize ? formatFileSize(meta.fileMaxSize) : "",
    openImagePicker,
    openFilePicker,
    onInputChange,
  }
}
