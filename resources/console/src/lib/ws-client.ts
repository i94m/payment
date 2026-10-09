type Handler = (data: unknown) => void

export class WsClient {
  private ws: WebSocket | null = null
  private url: string
  private getToken: () => string
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private listeners: Map<string, Set<Handler>> = new Map()
  private shouldReconnect = true

  constructor(url: string, getToken: () => string) {
    this.url = url
    this.getToken = getToken
  }

  connect() {
    this.shouldReconnect = true
    const token = this.getToken()
    if (!token) return

    const separator = this.url.includes("?") ? "&" : "?"
    const fullUrl = `${this.url}${separator}token=${token}`

    try {
      this.ws = new WebSocket(fullUrl)
    } catch {
      this.scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data)
        const type: string = msg.type || "*"
        const data: unknown = msg.data !== undefined ? msg.data : msg
        this.dispatch(type, data)
        this.dispatch("*", data)
      } catch {
        // ignore malformed messages
      }
    }

    this.ws.onclose = () => {
      this.ws = null
      if (this.shouldReconnect) {
        this.scheduleReconnect()
      }
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  on(type: string, handler: Handler): () => void {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set())
    }
    this.listeners.get(type)!.add(handler)
    return () => this.off(type, handler)
  }

  off(type: string, handler: Handler) {
    this.listeners.get(type)?.delete(handler)
  }

  send(type: string, data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, data }))
    }
  }

  close() {
    this.shouldReconnect = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  private dispatch(type: string, data: unknown) {
    this.listeners.get(type)?.forEach((h) => {
      try { h(data) } catch { /* ignore */ }
    })
  }

  private scheduleReconnect() {
    if (!this.shouldReconnect || this.reconnectAttempts >= this.maxReconnectAttempts) return
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }
}
