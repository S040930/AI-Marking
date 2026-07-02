#!/bin/bash
# 递归找出全局环境里"项目依赖闭包内"的孤儿包
# 不删除: pip / setuptools / wheel(基础工具)
# 不删除: 被项目闭包外的库引用的包(如 greenlet/pyee 被 playwright 引用)

GLOBAL_PIP=/opt/miniconda3/envs/cuda/bin/pip
PROJECT_PKGS_FILE=/tmp/venv_pkgs.txt

mapfile -t PROJECT_PKGS < "$PROJECT_PKGS_FILE"

# 基础工具白名单(永远不删)
NEVER_DELETE=("pip" "setuptools" "wheel")

is_never_delete() {
    local pkg="$1"
    for p in "${NEVER_DELETE[@]}"; do
        if [ "$p" = "$pkg" ]; then return 0; fi
    done
    return 1
}

declare -A WILL_DELETE

changed=true
while $changed; do
    changed=false
    for pkg in "${PROJECT_PKGS[@]}"; do
        if is_never_delete "$pkg"; then continue; fi
        if [ -n "${WILL_DELETE[$pkg]}" ]; then continue; fi
        required_by=$($GLOBAL_PIP show "$pkg" 2>/dev/null | awk -F': ' '/^Required-by:/ {print $2}')
        has_external=false
        IFS=', ' read -ra reqs <<< "$required_by"
        for r in "${reqs[@]}"; do
            if [ -z "$r" ]; then continue; fi
            if [ -n "${WILL_DELETE[$r]}" ]; then continue; fi
            has_external=true
            break
        done
        if [ "$has_external" = "false" ]; then
            WILL_DELETE["$pkg"]=1
            changed=true
        fi
    done
done

echo "=== 将被删除的包 ==="
delete_count=0
for pkg in "${PROJECT_PKGS[@]}"; do
    if [ -n "${WILL_DELETE[$pkg]}" ]; then
        echo "  $pkg"
        delete_count=$((delete_count + 1))
    fi
done
echo ""
echo "删除数量: $delete_count"
echo ""
echo "=== 保留的包(被全局其他库依赖或是基础工具)==="
for pkg in "${PROJECT_PKGS[@]}"; do
    if [ -z "${WILL_DELETE[$pkg]}" ]; then
        required_by=$($GLOBAL_PIP show "$pkg" 2>/dev/null | awk -F': ' '/^Required-by:/ {print $2}')
        echo "  $pkg  (Required-by: $required_by)"
    fi
done
