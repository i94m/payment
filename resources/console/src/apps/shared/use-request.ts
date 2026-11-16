import * as React from "react"
import { getErrorMessage } from "@/lib/error"

export interface RequestState<T> {
  data: T | null
  /** 已归一化的可展示错误文案；null 表示本次加载成功 */
  error: string | null
  loading: boolean
  /** 重新拉取（保留旧数据，加载态由 loading 表示） */
  reload: () => void
}

/**
 * 只读页面数据加载：loading / error / reload 三态齐备。
 *
 * 用于 dashboard、详情卡这类「一次加载 + 失败可重试」的场景，避免各页写成
 * `.catch(() => {})` 后加载失败只剩一屏 0 或永久骨架屏。列表分页仍走
 * `useDataList`（它自带分页与 onError）。
 *
 * `load` 通过 ref 调用，调用方不必 useCallback 包裹；需要随筛选条件重新拉取时
 * 在筛选变化处调用 `reload()`。
 */
export function useRequest<T>(load: () => Promise<T>): RequestState<T> {
  const [data, setData] = React.useState<T | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [tick, setTick] = React.useState(0)

  const loadRef = React.useRef(load)
  // 在 effect 里同步最新 loader（render 阶段写 ref 会触发 react-hooks/refs，
  // 且本 hook 的加载 effect 声明在下方，执行顺序保证读到的是最新一版）
  React.useEffect(() => {
    loadRef.current = load
  })

  React.useEffect(() => {
    let alive = true
    loadRef.current()
      .then((res) => {
        if (!alive) return
        setData(res)
        setError(null)
      })
      .catch((err) => {
        if (!alive) return
        setError(getErrorMessage(err, "加载失败，请稍后重试"))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [tick])

  // 只在这里同步置位加载态/清错（事件入口），effect 内不直接 setState
  const reload = React.useCallback(() => {
    setLoading(true)
    setError(null)
    setTick((n) => n + 1)
  }, [])

  return { data, error, loading, reload }
}
