"use client"

import * as React from "react"
import { EditorContent, useEditor } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import Image from "@tiptap/extension-image"
import { TableKit } from "@tiptap/extension-table"
import { TextStyleKit } from "@tiptap/extension-text-style"
import { Placeholder } from "@tiptap/extensions"
import FileHandler from "@tiptap/extension-file-handler"
import { cn } from "@/lib/utils"
import { EditorToolbar } from "./toolbar"
import { handleEditorDrop, handleEditorPaste, useEditorUpload } from "./use-editor-upload"
import "./rich-editor.css"

interface RichEditorProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  readOnly?: boolean
  /** 是否提供图片上传（默认开启） */
  uploadImage?: boolean
}

export function RichEditor({
  value,
  onChange,
  placeholder,
  readOnly,
  uploadImage = true,
}: RichEditorProps) {
  // 回调放进 ref：编辑器实例只创建一次，父组件重渲染不会导致内容/光标丢失
  const onChangeRef = React.useRef(onChange)
  const lastEmittedRef = React.useRef(value ?? "")

  // 隐藏 input 由组件持有，hook 只负责触发上传
  const imageInputRef = React.useRef<HTMLInputElement>(null)
  const fileInputRef = React.useRef<HTMLInputElement>(null)

  React.useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  const editor = useEditor(
    {
      // SSR/首帧不渲染，避免水合不一致
      immediatelyRender: false,
      editable: !readOnly,
      content: value || "",
      extensions: [
        StarterKit.configure({
          heading: { levels: [1, 2, 3] },
          link: {
            openOnClick: false,
            autolink: true,
            linkOnPaste: true,
            HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" },
          },
        }),
        Image.configure({ inline: false, allowBase64: false }),
        TableKit.configure({ table: { resizable: false } }),
        TextStyleKit.configure({ fontFamily: false, fontSize: false, lineHeight: false }),
        Placeholder.configure({ placeholder: placeholder || "" }),
        FileHandler.configure({
          onDrop: handleEditorDrop,
          onPaste: handleEditorPaste,
        }),
      ],
      editorProps: {
        attributes: { class: "rte-content" },
      },
      onUpdate: ({ editor }) => {
        const html = editor.getHTML()
        lastEmittedRef.current = html
        onChangeRef.current(html)
      },
    },
    [],
  )

  const upload = useEditorUpload(editor, { imageInputRef, fileInputRef })

  // 外部 value 变化回写（与自身 emit 的值相等时跳过，避免光标跳动）
  React.useEffect(() => {
    if (!editor) return
    const next = value ?? ""
    if (next === lastEmittedRef.current) return
    lastEmittedRef.current = next
    editor.commands.setContent(next, { emitUpdate: false })
  }, [editor, value])

  // 只读状态热切换
  React.useEffect(() => {
    editor?.setEditable(!readOnly)
  }, [editor, readOnly])

  return (
    <div className={cn("rich-editor-wrapper", readOnly && "is-readonly")}>
      {editor && !readOnly && (
        <EditorToolbar
          editor={editor}
          uploadImage={uploadImage}
          fileMaxSizeLabel={upload.fileMaxSizeLabel}
          onPickImage={upload.openImagePicker}
          onPickFile={upload.openFilePicker}
        />
      )}

      {editor ? (
        <EditorContent editor={editor} />
      ) : (
        <div className="min-h-[200px] px-4 py-3 text-sm text-muted-foreground">编辑器加载中…</div>
      )}

      {!readOnly && (
        <>
          <input
            ref={imageInputRef}
            type="file"
            accept={upload.imageAccept}
            multiple
            className="hidden"
            onChange={upload.onInputChange}
          />
          <input
            ref={fileInputRef}
            type="file"
            accept={upload.fileAccept || undefined}
            multiple
            className="hidden"
            onChange={upload.onInputChange}
          />
        </>
      )}
    </div>
  )
}
