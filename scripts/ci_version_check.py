#!/usr/bin/env python3
"""CI 发版前的版本号顺序校验（仅标准库，由 .github/workflows/docker-image.yml 调用）。

用途：防止「先发了 v1.0.2，又推 v1.0.1」把 `1.0` / `1` / `latest` 这些浮动标签回退。
比较的是仓库内的 git 标签（CI 只按标签构建，标签即已发布版本的事实来源）。

语义化排序要点：1.1.0 > 1.1.0-rc1 > 1.1.0-rc0，1.0.10 > 1.0.9。
不能用 `sort -V`：GNU 版本序里 1.1.0 < 1.1.0-rc1，会误拦 rc 之后的正式版。

退出码：0 可发布（stdout 打印仓库内已有最高版本，无标签时为空）；
        1 版本号回退或格式非法（错误信息走 stderr，带 GitHub `::error::` 注解）。

用法：
    python3 scripts/ci_version_check.py 1.0.3 --git-tags
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def parse(ver: str) -> tuple[int, int, int, int, tuple]:
    """返回可直接比较的元组；预发布标识符段按 semver 规则比较（数字段比字母段小）。"""
    m = SEMVER.match(ver)
    if not m:
        raise ValueError(ver)
    major, minor, patch, pre = m.groups()
    if pre is None:
        # 正式版排在所有同号预发布之后：1.1.0 > 1.1.0-rc1
        return (int(major), int(minor), int(patch), 1, ())
    parts = tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")
    )
    return (int(major), int(minor), int(patch), 0, parts)


def git_tags() -> list[str]:
    proc = subprocess.run(
        ["git", "tag", "-l", "v*"], capture_output=True, text=True, check=True
    )
    return [line.strip()[1:] for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="发版版本号顺序校验")
    ap.add_argument("version", help="本次要发布的版本号（不含 v 前缀）")
    ap.add_argument(
        "--git-tags",
        action="store_true",
        help="与仓库内已有的 v* 标签比较（缺失时视为无历史版本）",
    )
    args = ap.parse_args()

    try:
        candidate = parse(args.version)
    except ValueError:
        print(
            f"::error::'{args.version}' 不是 <major>.<minor>.<patch> 形式（如 1.2.3）",
            file=sys.stderr,
        )
        return 1

    if not args.git_tags:
        print(args.version)
        return 0

    existing = [(v, parse(v)) for v in git_tags() if SEMVER.match(v)]
    if not existing:
        print(args.version)
        return 0

    highest, highest_key = max(existing, key=lambda item: item[1])
    if candidate < highest_key:
        print(
            f"::error::仓库已有更高版本 v{highest}，拒绝发布 v{args.version}"
            "（版本号必须递增；确需补发旧版本请用手动 dispatch 并显式填版本号）",
            file=sys.stderr,
        )
        return 1

    print(highest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
