import * as React from "react"
import {
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type ColumnDef,
  type RowSelectionState,
  type Updater,
} from "@tanstack/react-table"
import type { PaginatedResponse } from "@/api"
import { useServerPagination } from "@/hooks/use-server-pagination"

interface UseDataListOptions<T> {
  fetchFn: (params: {
    page: number
    per_page: number
    keyword?: string
    [key: string]: unknown
  }) => Promise<PaginatedResponse<T>>
  columns: ColumnDef<T>[]
  perPage?: number
  onError?: (err: unknown) => void
  filters?: Record<string, unknown>
  /** 初始搜索词：列表页从别处带参跳转（如 ?trade_no=xx）时首屏即生效 */
  initialKeyword?: string
}

export function useDataList<T>({
  fetchFn,
  columns,
  perPage = 10,
  onError,
  filters,
  initialKeyword,
}: UseDataListOptions<T>) {
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({})
  const [deleteId, setDeleteId] = React.useState<number | null>(null)
  const [batchDeleteOpen, setBatchDeleteOpen] = React.useState(false)
  // 列表请求失败的错误对象：页面用它渲染内联错误态（ListError）而不是空表格
  const [error, setError] = React.useState<unknown>(null)

  // 包一层：记录错误供内联错误态使用，同时保留原 fetchFn 的异常语义（分页 hook 仍需 catch 通知）
  const fetchFnRef = React.useRef(fetchFn)
  fetchFnRef.current = fetchFn
  const trackedFetch = React.useCallback(
    async (params: { page: number; per_page: number; keyword?: string; [key: string]: unknown }) => {
      try {
        const res = await fetchFnRef.current(params)
        setError(null)
        return res
      } catch (err) {
        setError(err)
        throw err
      }
    },
    [],
  )

  const { data, total, keyword, appliedKeyword, loading, statistics, summary, setKeyword, applyFilters, refresh, updateRow, pagination, onPaginationChange, sorting, setSorting } =
    useServerPagination<T>({
      fetchFn: trackedFetch,
      perPage,
      onError,
      filters,
      initialKeyword,
    })

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualPagination: true,
    manualSorting: true,
    enableSortingRemoval: true,
    pageCount: Math.ceil(total / pagination.pageSize) || 1,
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onSortingChange: setSorting,
    onPaginationChange: (updater: Updater<{ pageIndex: number; pageSize: number }>) => {
      onPaginationChange(updater)
    },
    state: { rowSelection, pagination, sorting },
  })

  const selectedRows = table.getSelectedRowModel().rows.map((r) => r.original)
  const selectedCount = Object.keys(rowSelection).length
  const clearSelection = () => setRowSelection({})

  const reset = () => {
    setKeyword("")
    clearSelection()
    applyFilters()
  }

  const batchDelete = async (deleteFn: (items: T[]) => Promise<void>) => {
    try {
      await deleteFn(selectedRows)
      clearSelection()
      setBatchDeleteOpen(false)
      refresh()
    } catch (err) {
      // 把真实错误交给页面/对话框展示（原实现包装成英文 Error 会丢掉后端原因）
      onError?.(err)
      throw err
    }
  }

  return {
    table,
    data,
    total,
    loading,
    error,
    statistics,
    summary,
    keyword,
    appliedKeyword,
    setKeyword,
    applyFilters,
    refresh,
    updateRow,
    reset,
    pagination,
    onPaginationChange,
    sorting,
    setSorting,
    rowSelection,
    setRowSelection,
    selectedRows,
    selectedCount,
    clearSelection,
    deleteId,
    setDeleteId,
    batchDeleteOpen,
    setBatchDeleteOpen,
    batchDelete,
  }
}
