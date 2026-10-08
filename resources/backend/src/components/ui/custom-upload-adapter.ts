import apiClient from "@/lib/api-client"
import { getFileUrl } from "@/lib/storage"

interface StsConfig {
  provider: string
  prefix: string
  upload_url: string
  url: string
  payload: Record<string, string | number>
  success_action_status: number
  withCredentials?: boolean
}

function randomKey(prefix: string, file: File): string {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789"
  let str = ""
  for (let i = 0; i < 32; i++) str += chars[Math.floor(Math.random() * chars.length)]
  const ext = file.name.split(".").pop() || "bin"
  return prefix + str + "." + ext
}

async function fetchStsConfig(): Promise<StsConfig> {
  const res = await apiClient.get<any>("/upload/sts", { params: { policy: "image" } })
  const data = res.data ?? res
  if (data.provider === "local") {
    data.withCredentials = true
  }
  return data
}

export default class CustomUploadAdapter {
  private loader: any
  private xhr: XMLHttpRequest | null = null

  constructor(loader: any) {
    this.loader = loader
  }

  upload() {
    return this.loader.file.then(
      (file: File) =>
        new Promise((resolve, reject) => {
          fetchStsConfig()
            .then((config) => {
              this._initRequest(config)
              this._initListeners(resolve, reject, file, config)
              this._sendRequest(file, config)
            })
            .catch(reject)
        })
    )
  }

  abort() {
    this.xhr?.abort()
  }

  private _initRequest(config: StsConfig) {
    const xhr = (this.xhr = new XMLHttpRequest())
    xhr.open("POST", config.upload_url || config.url, true)
    xhr.withCredentials = !!config.withCredentials
    xhr.responseType = "json"
  }

  private _initListeners(
    resolve: (value: any) => void,
    reject: (reason?: any) => void,
    file: File,
    config: StsConfig
  ) {
    const xhr = this.xhr!
    const loader = this.loader
    const genericErrorText = `文件上传失败: ${file.name}.`

    xhr.addEventListener("error", () => reject(genericErrorText))
    xhr.addEventListener("abort", () => reject())

    xhr.addEventListener("load", () => {
      if (xhr.status !== config.success_action_status && xhr.status !== 200) {
        return reject(genericErrorText)
      }
      const key = (file as any)._uploadKey
      resolve({ default: getFileUrl(key) })
    })

    if (xhr.upload) {
      xhr.upload.addEventListener("progress", (evt) => {
        if (evt.lengthComputable) {
          loader.uploadTotal = evt.total
          loader.uploaded = evt.loaded
        }
      })
    }
  }

  private _sendRequest(file: File, config: StsConfig) {
    const data = new FormData()
    Object.entries(config.payload).forEach(([k, v]) => {
      data.append(k, String(v))
    })
    const key = randomKey(config.prefix, file)
    ;(file as any)._uploadKey = key
    data.append("key", key)
    data.append("file", file)
    this.xhr!.send(data)
  }
}

export function CustomUploadAdapterPlugin(editor: any) {
  editor.plugins.get("FileRepository").createUploadAdapter = (loader: any) => {
    return new CustomUploadAdapter(loader)
  }
}
