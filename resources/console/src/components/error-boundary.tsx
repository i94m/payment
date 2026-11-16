"use client"

import { Component } from "react"
import type { ReactNode, ErrorInfo } from "react"
import { IconExclamationCircle } from "@tabler/icons-react"
import { ErrorPage, GoBackButton } from "@/pages/errors/error-page"
import { Button } from "@/components/ui/button"

interface Props {
  children: ReactNode
  /**
   * 变化时清除错误态：路由级用法传入 `location.pathname`，
   * 一次页面崩溃不会把用户永久锁在兜底页（切换菜单即恢复）。
   */
  resetKey?: unknown
  /** 自定义兜底渲染；缺省为整页错误提示（重新加载 / 返回上页） */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack)
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.reset()
    }
  }

  reset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (!this.state.hasError) return this.props.children

    const error = this.state.error ?? new Error("未知错误")
    if (this.props.fallback) return this.props.fallback(error, this.reset)

    return (
      <ErrorPage
        icon={<IconExclamationCircle className="size-16" />}
        title="页面出了点问题"
        description={error.message || "发生了未知错误"}
      >
        <div className="flex items-center gap-2">
          <GoBackButton />
          <Button variant="outline" onClick={() => window.location.reload()}>
            重新加载
          </Button>
        </div>
      </ErrorPage>
    )
  }
}
