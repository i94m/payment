import * as React from "react"
import {
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type ColumnDef,
  type RowSelectionState,
  type SortingState,
  type Updater,
} from "@tanstack/react-table"
import type { PaginatedResponse, StatisticItem } from "@/api"
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
}

export function useDataList<T>({
  fetchFn,
  columns,
  perPage = 10,
  onError,
  filters,
}: UseDataListOptions<T>) {
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({})
  const [deleteId, setDeleteId] = React.useState<number | null>(null)
  const [batchDeleteOpen, setBatchDeleteOpen] = React.useState(false)

  const { data, total, keyword, appliedKeyword, loading, statistics, summary, setKeyword, applyFilters, refresh, updateRow, pagination, onPaginationChange, sorting, setSorting } =
    useServerPagination<T>({
      fetchFn,
      perPage,
      onError,
      filters,
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
    } catch {
      onError?.(new Error("Batch delete failed"))
    }
  }

  return {
    table,
    data,
    total,
    loading,
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
