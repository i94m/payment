"""Excel(xlsx) 导出工具"""
import io
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

# xlsx 的 MIME 类型（前端据此直接下载）
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def export_xlsx(
    items: list[dict],
    filename: str,
    columns: list[tuple[str, str]] | None = None,
) -> StreamingResponse:
    """
    导出 Excel(.xlsx) 文件

    Args:
        items: 数据列表 (list of dict)
        filename: 下载文件名（应带 .xlsx 后缀）
        columns: 列定义 [(key, header_name), ...]，None 则用 dict keys
    """
    if columns:
        # 兼容两种形态：list[tuple[key, header]] 或 list[str]（key 即表头）
        if isinstance(columns[0], str):
            headers = keys = list(columns)  # type: ignore[assignment]
        else:
            headers = [c[1] for c in columns]
            keys = [c[0] for c in columns]
    elif items:
        headers = list(items[0].keys())
        keys = headers
    else:
        headers = keys = []

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(headers)

    widths = [len(str(h)) for h in headers]
    for item in items:
        line = []
        for idx, k in enumerate(keys):
            v = item.get(k, "")
            text = "" if v is None else str(v)
            # 超长文本（备注/参数等）按 Excel 单元格上限截断
            if len(text) > 32767:
                text = text[:32767]
            line.append(text)
            if idx < len(widths):
                widths[idx] = max(widths[idx], len(text))
        ws.append(line)

    for idx, width in enumerate(widths, start=1):
        # 中文字符按 2 个宽度估算，最宽 60
        ws.column_dimensions[get_column_letter(idx)].width = min(width + 2, 60)
    ws.freeze_panes = "A2"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
