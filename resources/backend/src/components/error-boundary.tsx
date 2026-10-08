"use client"

import { Component } from "react"
import type { ReactNode, ErrorInfo } from "react"
import { IconExclamationCircle } from "@tabler/icons-react"
import { ErrorPage } from "@/pages/errors/error-page"
import { Button } from "@/components/ui/button"

interface Props {
  children: ReactNode
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

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <ErrorPage
        icon={<IconExclamationCircle className="size-16" />}
        title="页面出了点问题"
        description={this.state.error?.message || "发生了未知错误"}
      >
        <Button variant="outline" onClick={() => window.location.reload()}>
          重新加载
        </Button>
      </ErrorPage>
    )
  }
}
