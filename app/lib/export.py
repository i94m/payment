"""CSV 导出工具"""
import csv
import io
from fastapi.responses import StreamingResponse


def export_csv(items: list[dict], filename: str, columns: list[tuple[str, str]] | None = None) -> StreamingResponse:
    """
    导出 CSV 文件

    Args:
        items: 数据列表 (list of dict)
        filename: 下载文件名
        columns: 列定义 [(key, header_name), ...], None 则用 dict keys
    """
    output = io.StringIO()
    if not items:
        output.write("")
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    if columns:
        # 兼容两种形态：list[tuple[key, header]] 或 list[str]（key 即表头）
        if isinstance(columns[0], str):
            headers = keys = list(columns)  # type: ignore[assignment]
        else:
            headers = [c[1] for c in columns]
            keys = [c[0] for c in columns]
    else:
        headers = list(items[0].keys())
        keys = headers

    writer = csv.DictWriter(output, fieldnames=keys)
    # write BOM for Excel compatibility
    output.write('\ufeff')
    writer.writerow(dict(zip(keys, headers)))
    for item in items:
        row = {}
        for k in keys:
            v = item.get(k, "")
            row[k] = str(v) if v is not None else ""
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
