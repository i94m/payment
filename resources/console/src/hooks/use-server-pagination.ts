import * as React from "react"
import type { PaginatedResponse, StatisticItem } from "@/api"
import type { Updater, SortingState } from "@tanstack/react-table"

interface UseServerPaginationOptions<T> {
  fetchFn: (params: {
    page: number
    per_page: number
    keyword?: string
    order_by?: string
    [key: string]: unknown
  }) => Promise<PaginatedResponse<T>>
  perPage?: number
  onError?: (err: unknown) => void
  filters?: Record<string, unknown>
}

export function useServerPagination<T>({
  fetchFn,
  perPage = 10,
  onError,
  filters,
}: UseServerPaginationOptions<T>) {
  const [data, setData] = React.useState<T[]>([])
  const [total, setTotal] = React.useState(0)
  const [totalPages, setTotalPages] = React.useState(1)
  const [currentPage, setCurrentPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState(perPage)
  // `keyword` is the DRAFT value bound to the search input — typing must NOT
  // trigger a request. `appliedKeyword` is the committed value sent in requests.
  const keywordRef = React.useRef("")
  const appliedKeywordRef = React.useRef("")
  const [keyword, setKeywordState] = React.useState("")
  const [appliedKeyword, setAppliedKeyword] = React.useState("")
  const [sorting, setSorting] = React.useState<SortingState>([])
  const [loading, setLoading] = React.useState(true)
  const [statistics, setStatistics] = React.useState<StatisticItem[] | undefined>(undefined)
  const [summary, setSummary] = React.useState<Record<string, unknown> | undefined>(undefined)
  // Bumped by applyFilters() so a search always fires exactly one request,
  // even when the draft keyword / page haven't actually changed.
  const [fetchTrigger, setFetchTrigger] = React.useState(0)

  const fetchFnRef = React.useRef(fetchFn)
  fetchFnRef.current = fetchFn
  const onErrorRef = React.useRef(onError)
  onErrorRef.current = onError
  const filtersRef = React.useRef(filters)
  filtersRef.current = filters

  const orderByParam = React.useMemo(() => {
    if (sorting.length === 0) return undefined
    const s = sorting[0]
    return s.desc ? `-${s.id}` : s.id
  }, [sorting])

  const fetchData = React.useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchFnRef.current({
        page: currentPage,
        per_page: pageSize,
        keyword: appliedKeywordRef.current || undefined,
        order_by: orderByParam,
        ...filtersRef.current,
      })
      setData(res.items)
      setTotal(res.total)
      setTotalPages(res.total_page)
      setStatistics(res.statistics)
      setSummary(res.summary)
    } catch (err) {
      onErrorRef.current?.(err)
    } finally {
      setLoading(false)
    }
  }, [currentPage, pageSize, orderByParam, fetchTrigger])

  React.useEffect(() => {
    fetchData()
  }, [fetchData])

  // Update the search input only — no request, no page change.
  const setKeyword = (value: string) => {
    keywordRef.current = value
    setKeywordState(value)
  }

  // Commit the current draft (keyword + filters) and fetch. This is the only
  // way a search/filter change triggers a request. Reads from refs so the
  // callback stays stable; the fetch runs in the post-render effect, by which
  // point filtersRef/fetchFnRef already reflect the latest state — which makes
  // reset-then-apply correct without any extra plumbing.
  const applyFilters = React.useCallback(() => {
    appliedKeywordRef.current = keywordRef.current
    setAppliedKeyword(keywordRef.current)
    setCurrentPage(1)
    setFetchTrigger((n) => n + 1)
  }, [])

  const setPage = (page: number) => {
    setCurrentPage(page)
  }

  const pagination = React.useMemo(
    () => ({ pageIndex: currentPage - 1, pageSize }),
    [currentPage, pageSize]
  )

  const updateRow = React.useCallback((id: number, patch: Partial<T>) => {
    setData((prev) => prev.map((item) => (item as { id?: number }).id === id ? { ...item, ...patch } : item))
  }, [])

  const onPaginationChange = React.useCallback(
    (updater: Updater<{ pageIndex: number; pageSize: number }>) => {
      const next =
        typeof updater === "function"
          ? updater({ pageIndex: currentPage - 1, pageSize })
          : updater
      setCurrentPage(next.pageIndex + 1)
      setPageSize(next.pageSize)
    },
    [currentPage, pageSize]
  )

  return {
    data,
    total,
    totalPages,
    currentPage,
    pageSize,
    keyword,
    appliedKeyword,
    sorting,
    setSorting,
    loading,
    statistics,
    summary,
    setKeyword,
    applyFilters,
    setPage,
    refresh: fetchData,
    updateRow,
    pagination,
    onPaginationChange,
  }
}
