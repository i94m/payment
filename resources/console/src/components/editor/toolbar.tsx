"use client"

import * as React from "react"
import type { Editor } from "@tiptap/react"
import { useEditorState } from "@tiptap/react"
import {
  IconArrowBackUp,
  IconArrowForwardUp,
  IconBlockquote,
  IconBold,
  IconChevronDown,
  IconClearFormatting,
  IconCode,
  IconColumnInsertLeft,
  IconColumnInsertRight,
  IconColumnRemove,
  IconH1,
  IconH2,
  IconH3,
  IconHighlight,
  IconItalic,
  IconLink,
  IconList,
  IconListNumbers,
  IconMinus,
  IconPalette,
  IconPaperclip,
  IconPhoto,
  IconPilcrow,
  IconRowInsertBottom,
  IconRowInsertTop,
  IconRowRemove,
  IconStrikethrough,
  IconTable,
  IconTableOff,
  IconUnderline,
  IconUnlink,
} from "@tabler/icons-react"
import { Toggle } from "@/components/ui/toggle"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const PRESET_COLORS = [
  "#000000", "#374151", "#6b7280", "#d1d5db", "#ffffff",
  "#ef4444", "#f97316", "#f59e0b", "#22c55e", "#06b6d4",
  "#3b82f6", "#6366f1", "#8b5cf6", "#d946ef", "#ec4899",
]

/** 工具栏按钮（带 tooltip 与按下态） */
function ToolbarButton({
  label,
  pressed,
  disabled,
  onClick,
  children,
}: {
  label: string
  pressed?: boolean
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Toggle
            size="sm"
            pressed={!!pressed}
            disabled={disabled}
            aria-label={label}
            onPressedChange={() => onClick()}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

/** 标题/正文选择 */
function BlockMenu({ editor }: { editor: Editor }) {
  const current = useEditorState({
    editor,
    selector: ({ editor }) => {
      for (const level of [1, 2, 3] as const) {
        if (editor.isActive("heading", { level })) return `h${level}`
      }
      return "p"
    },
  })

  const label = current === "p" ? "正文" : `标题 ${current.slice(1)}`

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button type="button" variant="ghost" size="sm" className="gap-1 px-2 text-xs" />}
      >
        {label}
        <IconChevronDown className="size-3.5 opacity-60" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-36">
        <DropdownMenuItem onClick={() => editor.chain().focus().setParagraph().run()}>
          <IconPilcrow className="size-4" />
          正文
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>
          <IconH1 className="size-4" />
          标题 1
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
          <IconH2 className="size-4" />
          标题 2
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
          <IconH3 className="size-4" />
          标题 3
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 颜色选择（文字色 / 背景色） */
function ColorMenu({
  label,
  icon,
  value,
  onChange,
  onClear,
}: {
  label: string
  icon: React.ReactNode
  value?: string
  onChange: (color: string) => void
  onClear: () => void
}) {
  const [open, setOpen] = React.useState(false)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger
          render={<PopoverTrigger render={<Toggle size="sm" pressed={open} aria-label={label} />} />}
        >
          <span className="relative flex items-center">
            {icon}
            <span
              className="absolute -bottom-0.5 left-0 h-0.5 w-3.5 rounded-full"
              style={{ backgroundColor: value || "currentColor" }}
            />
          </span>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-auto p-3">
        <div className="mb-2 grid grid-cols-5 gap-1.5">
          {PRESET_COLORS.map((color) => (
            <button
              key={color}
              type="button"
              className={cn(
                "size-6 rounded-md border transition-transform hover:scale-110",
                value === color && "ring-2 ring-ring ring-offset-1",
              )}
              style={{ backgroundColor: color }}
              aria-label={color}
              onClick={() => {
                onChange(color)
                setOpen(false)
              }}
            />
          ))}
        </div>
        <div className="flex items-center justify-between gap-2">
          <input
            type="color"
            value={value || "#000000"}
            onChange={(e) => onChange(e.target.value)}
            className="size-7 cursor-pointer border-0 bg-transparent p-0"
            aria-label="自定义颜色"
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              onClear()
              setOpen(false)
            }}
          >
            清除
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** 链接设置 */
function LinkMenu({ editor }: { editor: Editor }) {
  const [open, setOpen] = React.useState(false)
  const [href, setHref] = React.useState("")
  const active = useEditorState({
    editor,
    selector: ({ editor }) => editor.isActive("link"),
  })

  const normalize = (raw: string) => {
    const value = raw.trim()
    if (!value) return ""
    return /^(https?:|mailto:|tel:|#|\/)/i.test(value) ? value : `https://${value}`
  }

  const apply = () => {
    const next = normalize(href)
    if (!next) {
      editor.chain().focus().extendMarkRange("link").unsetLink().run()
    } else {
      editor.chain().focus().extendMarkRange("link").setLink({ href: next }).run()
    }
    setOpen(false)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) setHref((editor.getAttributes("link").href as string) ?? "")
      }}
    >
      <Tooltip>
        <TooltipTrigger
          render={<PopoverTrigger render={<Toggle size="sm" pressed={active} aria-label="链接" />} />}
        >
          <IconLink />
        </TooltipTrigger>
        <TooltipContent>链接</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-72 space-y-2 p-3">
        <Input
          value={href}
          placeholder="https://example.com"
          onChange={(e) => setHref(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              apply()
            }
          }}
        />
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              editor.chain().focus().extendMarkRange("link").unsetLink().run()
              setOpen(false)
            }}
          >
            移除链接
          </Button>
          <Button type="button" size="sm" onClick={apply}>
            确定
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** 表格操作 */
function TableMenu({ editor, pressed }: { editor: Editor; pressed: boolean }) {
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger
          render={<DropdownMenuTrigger render={<Toggle size="sm" pressed={pressed} aria-label="表格" />} />}
        >
          <IconTable />
        </TooltipTrigger>
        <TooltipContent>表格</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="start" className="w-44">
        <DropdownMenuItem onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>
          <IconTable className="size-4" />
          插入表格
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-xs text-muted-foreground">行</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => editor.chain().focus().addRowBefore().run()}>
            <IconRowInsertTop className="size-4" />
            上方插入行
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().addRowAfter().run()}>
            <IconRowInsertBottom className="size-4" />
            下方插入行
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().deleteRow().run()}>
            <IconRowRemove className="size-4" />
            删除当前行
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-xs text-muted-foreground">列</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => editor.chain().focus().addColumnBefore().run()}>
            <IconColumnInsertLeft className="size-4" />
            左侧插入列
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().addColumnAfter().run()}>
            <IconColumnInsertRight className="size-4" />
            右侧插入列
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().deleteColumn().run()}>
            <IconColumnRemove className="size-4" />
            删除当前列
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeaderRow().run()}>
          切换表头行
        </DropdownMenuItem>
        <DropdownMenuItem variant="destructive" onClick={() => editor.chain().focus().deleteTable().run()}>
          <IconTableOff className="size-4" />
          删除表格
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

interface EditorToolbarProps {
  editor: Editor
  /** 是否允许插入图片（对应 RichEditor 的 uploadImage） */
  uploadImage?: boolean
  /** 文件大小提示，如 50MB */
  fileMaxSizeLabel?: string
  onPickImage: () => void
  onPickFile: () => void
}

export function EditorToolbar({
  editor,
  uploadImage = true,
  fileMaxSizeLabel,
  onPickImage,
  onPickFile,
}: EditorToolbarProps) {
  const state = useEditorState({
    editor,
    selector: ({ editor }) => ({
      bold: editor.isActive("bold"),
      italic: editor.isActive("italic"),
      underline: editor.isActive("underline"),
      strike: editor.isActive("strike"),
      bulletList: editor.isActive("bulletList"),
      orderedList: editor.isActive("orderedList"),
      blockquote: editor.isActive("blockquote"),
      codeBlock: editor.isActive("codeBlock"),
      color: editor.getAttributes("textStyle").color as string | undefined,
      backgroundColor: editor.getAttributes("textStyle").backgroundColor as string | undefined,
      link: editor.isActive("link"),
      table: editor.isActive("table"),
      canUndo: editor.can().undo(),
      canRedo: editor.can().redo(),
    }),
  })

  return (
    <TooltipProvider delay={300}>
      <div className="flex flex-wrap items-center gap-0.5 border-b bg-muted/30 px-2 py-1.5">
      <ToolbarButton label="撤销" disabled={!state.canUndo} onClick={() => editor.chain().focus().undo().run()}>
        <IconArrowBackUp />
      </ToolbarButton>
      <ToolbarButton label="重做" disabled={!state.canRedo} onClick={() => editor.chain().focus().redo().run()}>
        <IconArrowForwardUp />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <BlockMenu editor={editor} />

      <Separator orientation="vertical" className="mx-1 h-6" />

      <ToolbarButton label="加粗" pressed={state.bold} onClick={() => editor.chain().focus().toggleBold().run()}>
        <IconBold />
      </ToolbarButton>
      <ToolbarButton label="斜体" pressed={state.italic} onClick={() => editor.chain().focus().toggleItalic().run()}>
        <IconItalic />
      </ToolbarButton>
      <ToolbarButton label="下划线" pressed={state.underline} onClick={() => editor.chain().focus().toggleUnderline().run()}>
        <IconUnderline />
      </ToolbarButton>
      <ToolbarButton label="删除线" pressed={state.strike} onClick={() => editor.chain().focus().toggleStrike().run()}>
        <IconStrikethrough />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <ColorMenu
        label="文字颜色"
        icon={<IconPalette />}
        value={state.color}
        onChange={(color) => editor.chain().focus().setColor(color).run()}
        onClear={() => editor.chain().focus().unsetColor().run()}
      />
      <ColorMenu
        label="背景颜色"
        icon={<IconHighlight />}
        value={state.backgroundColor}
        onChange={(color) => editor.chain().focus().setBackgroundColor(color).run()}
        onClear={() => editor.chain().focus().unsetBackgroundColor().run()}
      />

      <Separator orientation="vertical" className="mx-1 h-6" />

      <ToolbarButton label="无序列表" pressed={state.bulletList} onClick={() => editor.chain().focus().toggleBulletList().run()}>
        <IconList />
      </ToolbarButton>
      <ToolbarButton label="有序列表" pressed={state.orderedList} onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        <IconListNumbers />
      </ToolbarButton>
      <ToolbarButton label="引用" pressed={state.blockquote} onClick={() => editor.chain().focus().toggleBlockquote().run()}>
        <IconBlockquote />
      </ToolbarButton>
      <ToolbarButton label="代码块" pressed={state.codeBlock} onClick={() => editor.chain().focus().toggleCodeBlock().run()}>
        <IconCode />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <LinkMenu editor={editor} />
      <ToolbarButton label="移除链接" disabled={!state.link} onClick={() => editor.chain().focus().unsetLink().run()}>
        <IconUnlink />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      {uploadImage && (
        <ToolbarButton label="插入图片" onClick={onPickImage}>
          <IconPhoto />
        </ToolbarButton>
      )}
      <ToolbarButton
        label={fileMaxSizeLabel ? `插入文件（不超过 ${fileMaxSizeLabel}）` : "插入文件"}
        onClick={onPickFile}
      >
        <IconPaperclip />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <TableMenu editor={editor} pressed={state.table} />
      <ToolbarButton label="分割线" onClick={() => editor.chain().focus().setHorizontalRule().run()}>
        <IconMinus />
      </ToolbarButton>
      <ToolbarButton
        label="清除格式"
        onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}
      >
        <IconClearFormatting />
      </ToolbarButton>
      </div>
    </TooltipProvider>
  )
}
