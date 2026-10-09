import * as React from "react"
import { cn } from "@/lib/utils"

function Timeline({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline"
      className={cn("flex flex-col", className)}
      {...props}
    />
  )
}

function TimelineItem({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-item"
      className={cn("group flex gap-4", className)}
      {...props}
    />
  )
}

function TimelineSeparator({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-separator"
      className={cn("flex flex-col items-center", className)}
      {...props}
    />
  )
}

function TimelineDot({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-dot"
      className={cn(
        "mt-1.5 size-3 rounded-full border-2 border-primary bg-background shrink-0",
        "group-last:bg-primary",
        className,
      )}
      {...props}
    />
  )
}

function TimelineLine({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-line"
      className={cn("flex-1 border-l-2 border-border group-last:border-0", className)}
      {...props}
    />
  )
}

function TimelineContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-content"
      className={cn("flex-1 pb-6 group-last:pb-0", className)}
      {...props}
    />
  )
}

function TimelineHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-header"
      className={cn("flex items-center gap-2", className)}
      {...props}
    />
  )
}

function TimelineTitle({ className, ...props }: React.ComponentProps<"h4">) {
  return (
    <h4
      data-slot="timeline-title"
      className={cn("text-sm font-medium leading-none", className)}
      {...props}
    />
  )
}

function TimelineDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="timeline-description"
      className={cn("text-sm text-muted-foreground mt-1", className)}
      {...props}
    />
  )
}

export {
  Timeline,
  TimelineItem,
  TimelineSeparator,
  TimelineDot,
  TimelineLine,
  TimelineContent,
  TimelineHeader,
  TimelineTitle,
  TimelineDescription,
}
