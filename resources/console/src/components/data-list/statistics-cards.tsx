import type { StatisticItem } from "@/api"

interface StatisticsCardsProps {
  statistics?: StatisticItem[]
}

export function StatisticsCards({ statistics }: StatisticsCardsProps) {
  if (!statistics?.length) return null

  return (
    <div className="flex gap-3">
      {statistics.map((item) => (
        <div
          key={item.label}
          className="rounded-lg border bg-card px-4 py-3"
        >
          <div className="text-xl font-semibold tabular-nums" style={{ color: item.color || "hsl(var(--primary))" }}>
            {typeof item.value === "number" ? item.value.toLocaleString() : item.value}
            <span className="ml-1 text-sm font-normal text-muted-foreground">{item.unit}</span>
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">{item.label}</div>
        </div>
      ))}
    </div>
  )
}
