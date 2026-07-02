#!/usr/bin/env python3
"""递归找出全局环境里"项目依赖闭包内"的孤儿包,生成可删除清单。
不删除: pip / setuptools / wheel(基础工具)
不删除: 被项目闭包外的库引用的包(如 greenlet/pyee 被 playwright 引用)
"""
import subprocess
import sys

GLOBAL_PIP = "/opt/miniconda3/envs/cuda/bin/pip"
PROJECT_PKGS_FILE = "/tmp/venv_pkgs.txt"
NEVER_DELETE = {"pip", "setuptools", "wheel"}


def get_required_by(pkg: str) -> list[str]:
    try:
        out = subprocess.run(
            [GLOBAL_PIP, "show", pkg],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        return []
    for line in out.stdout.splitlines():
        if line.startswith("Required-by:"):
            val = line.split(":", 1)[1].strip()
            if not val:
                return []
            return [x.strip() for x in val.split(",") if x.strip()]
    return []


def main() -> None:
    with open(PROJECT_PKGS_FILE) as f:
        project_pkgs = [line.strip() for line in f if line.strip()]

    will_delete: set[str] = set()

    changed = True
    while changed:
        changed = False
        for pkg in project_pkgs:
            if pkg in will_delete or pkg in NEVER_DELETE:
                continue
            reqs = get_required_by(pkg)
            # 过滤掉已标记删除的引用者
            external = [r for r in reqs if r not in will_delete]
            if not external:
                will_delete.add(pkg)
                changed = True

    print("=== 将被删除的包 ===")
    for pkg in project_pkgs:
        if pkg in will_delete:
            print(f"  {pkg}")
    print(f"\n删除数量: {len(will_delete)}")

    print("\n=== 保留的包(被全局其他库依赖或是基础工具)===")
    for pkg in project_pkgs:
        if pkg not in will_delete:
            reqs = get_required_by(pkg)
            print(f"  {pkg}  (Required-by: {', '.join(reqs)})")


if __name__ == "__main__":
    main()
