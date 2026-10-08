import { type Table } from "@tanstack/react-table"
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

interface DataTablePaginationProps<TData> {
  table: Table<TData>
  pageSizeOptions?: number[]
}

function getPageNumbers(current: number, total: number, delta = 2): (number | "...")[] {
  if (total <= 1) return [1]
  const range: (number | "...")[] = []
  const left = Math.max(2, current - delta)
  const right = Math.min(total - 1, current + delta)

  range.push(1)
  if (left > 2) range.push("...")
  for (let i = left; i <= right; i++) range.push(i)
  if (right < total - 1) range.push("...")
  if (total > 1) range.push(total)

  return range
}

export function DataTablePagination<TData>({
  table,
  pageSizeOptions = [10, 20, 30, 50, 100],
}: DataTablePaginationProps<TData>) {
  const selectedCount = table.getFilteredSelectedRowModel().rows.length
  const totalRows = table.getFilteredRowModel().rows.length
  const current = table.getState().pagination.pageIndex + 1
  const totalPages = table.getPageCount()
  const pages = getPageNumbers(current, totalPages)

  const handleJumpPage = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const input = form.querySelector("input") as HTMLInputElement
    const page = parseInt(input.value, 10)
    if (page >= 1 && page <= totalPages) {
      table.setPageIndex(page - 1)
    }
    input.value = ""
    input.blur()
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t px-4 py-3">
      <div className="flex items-center gap-4">
        <div className="text-sm text-muted-foreground">
          {selectedCount > 0 ? (
            <>{selectedCount} {"已选择"} / {totalRows}</>
          ) : (
            <>共 {totalRows} 条</>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <p className="text-sm font-medium">{"每页行数"}</p>
          <Select
            value={`${table.getState().pagination.pageSize}`}
            onValueChange={(value) => {
              table.setPageSize(Number(value))
            }}
          >
            <SelectTrigger className="h-8 w-[70px]">
              <SelectValue placeholder={table.getState().pagination.pageSize} />
            </SelectTrigger>
            <SelectContent side="top">
              {pageSizeOptions.map((pageSize) => (
                <SelectItem key={pageSize} value={`${pageSize}`}>
                  {pageSize}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="flex items-center space-x-1">
        <Button
          variant="outline"
          size="icon"
          className="hidden h-8 w-8 lg:flex"
          onClick={() => table.setPageIndex(0)}
          disabled={!table.getCanPreviousPage()}
        >
          <ChevronsLeftIcon className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          onClick={() => table.previousPage()}
          disabled={!table.getCanPreviousPage()}
        >
          <ChevronLeftIcon className="h-4 w-4" />
        </Button>
        {pages.map((page, i) =>
          page === "..." ? (
            <span key={`ellipsis-${i}`} className="px-1 text-sm text-muted-foreground">...</span>
          ) : (
            <Button
              key={page}
              variant={page === current ? "default" : "outline"}
              size="icon"
              className={cn("h-8 w-8 text-sm", page === current && "pointer-events-none")}
              onClick={() => table.setPageIndex(page - 1)}
            >
              {page}
            </Button>
          )
        )}
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          onClick={() => table.nextPage()}
          disabled={!table.getCanNextPage()}
        >
          <ChevronRightIcon className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="hidden h-8 w-8 lg:flex"
          onClick={() => table.setPageIndex(table.getPageCount() - 1)}
          disabled={!table.getCanNextPage()}
        >
          <ChevronsRightIcon className="h-4 w-4" />
        </Button>
        {totalPages > 1 && (
          <form onSubmit={handleJumpPage} className="flex items-center gap-1 ml-2">
            <span className="text-sm text-muted-foreground">跳转</span>
            <input
              type="number"
              min={1}
              max={totalPages}
              className="h-8 w-14 rounded-md border border-input bg-card px-2 text-sm text-center [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
              placeholder={`${current}`}
            />
            <span className="text-sm text-muted-foreground">页</span>
          </form>
        )}
      </div>
    </div>
  )
}
