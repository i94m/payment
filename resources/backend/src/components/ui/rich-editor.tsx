import { CKEditor } from "@ckeditor/ckeditor5-react"
import ZH_CN from "ckeditor5/translations/zh-cn.js"
import {
  ClassicEditor,
  Undo,
  Heading,
  Bold,
  Link,
  Italic,
  Image,
  ImageUpload,
  ImageUploadUI,
  Table,
  TableToolbar,
  FontColor,
  FontBackgroundColor,
  List,
  ListProperties,
  HorizontalLine,
  RemoveFormat,
  SourceEditing,
  GeneralHtmlSupport,
} from "ckeditor5"
import "ckeditor5/ckeditor5.css"
import "./rich-editor.css"
import { CustomUploadAdapterPlugin } from "./custom-upload-adapter"

interface RichEditorProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  readOnly?: boolean
  uploadImage?: boolean
}

const plugins = [
  Undo,
  Heading,
  Bold,
  Link,
  Italic,
  Image,
  ImageUpload,
  ImageUploadUI,
  Table,
  TableToolbar,
  FontColor,
  FontBackgroundColor,
  List,
  ListProperties,
  HorizontalLine,
  RemoveFormat,
  SourceEditing,
  GeneralHtmlSupport,
  CustomUploadAdapterPlugin,
]

export function RichEditor({ value, onChange, placeholder, readOnly, uploadImage = true }: RichEditorProps) {
  const toolbarItems = [
    "undo", "redo", "|",
    "heading", "|",
    "bold", "italic", "|",
    "fontColor", "fontBackgroundColor", "|",
    "bulletedList", "numberedList", "|",
    "link",
    ...(uploadImage ? ["imageUpload"] : []),
    "insertTable", "horizontalLine", "|",
    "removeFormat", "sourceEditing",
  ]

  return (
    <div className="rich-editor-wrapper">
      <CKEditor
        editor={ClassicEditor}
        data={value}
        config={{
          plugins,
          translations: [ZH_CN],
          language: { ui: "zh-cn" },
          placeholder: placeholder || "",
          toolbar: {
            items: toolbarItems,
            shouldNotGroupWhenFull: true,
          },
        }}
        disabled={readOnly}
        onChange={(_event, editor) => {
          onChange(editor.getData())
        }}
      />
    </div>
  )
}
