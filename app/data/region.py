"""行政区划查询

数据源: app/data/regions.json（db init 时导入到 regions 表）
数据出处: https://github.com/modood/Administrative-divisions-of-China
"""

from app.models import Region


def build_tree(items: list[Region], level: int = 3) -> list[dict]:
    """
    将扁平 Region 列表构建为 Arco Design Cascader 树形结构

    Args:
        items: Region 列表
        level: 层级深度 (1=省, 2=省市, 3=省市区)

    Returns:
        [{"label": "广东省", "value": "44", "children": [...]}]
    """
    # 按 id 建索引
    by_id = {r.id: r for r in items}

    # 按父级分组
    children_map: dict[int, list[dict]] = {}
    for item in items:
        if item.level > level:
            continue
        node = {"label": item.name, "value": item.code}
        if item.level < level:
            node["children"] = []  # 先占位，后面填充
        children_map.setdefault(item.pid, []).append(node)

    # 递归填充 children（引用替换）
    def _fill(nodes: list[dict]) -> None:
        for node in nodes:
            if "children" in node:
                # 通过 code 反查 id，再从 children_map 取子节点
                target = next((r for r in items if r.code == node["value"] and r.level < level), None)
                if target:
                    child_nodes = children_map.get(target.id, [])
                    node["children"] = child_nodes
                    _fill(child_nodes)

    tree = children_map.get(0, [])
    _fill(tree)
    return tree
