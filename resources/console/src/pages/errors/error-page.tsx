import type { ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { useNavigate } from "react-router-dom"
import { cn } from "@/lib/utils"

interface ErrorPageProps {
  icon: ReactNode
  title: string
  description: string
  className?: string
  children?: ReactNode
}

export function ErrorPage({ icon, title, description, className, children }: ErrorPageProps) {
  return (
    <div className={cn("flex flex-1 items-center justify-center p-8", className)}>
      <div className="flex flex-col items-center text-center max-w-md">
        <div className="text-muted-foreground mb-4">{icon}</div>
        <h1 className="text-xl font-semibold mb-2">{title}</h1>
        <p className="text-sm text-muted-foreground mb-6">{description}</p>
        {children}
      </div>
    </div>
  )
}

export function GoHomeButton() {
  const navigate = useNavigate()
  return <Button variant="outline" onClick={() => navigate("/")}>返回首页</Button>
}

export function GoBackButton() {
  const navigate = useNavigate()
  return <Button variant="outline" onClick={() => navigate(-1)}>返回上页</Button>
}
