#!/usr/bin/env bash
# 部署 §15.6 的执行身份模型。**幂等，可重复跑，而且必须重复跑。**
#
# 为什么必须重复跑：`git reset --hard` / `git checkout` 重写文件时会把属主打回
# 执行 git 的那个用户（服务器上是 root）。**权限不是仓库的一部分**，
# 它是部署的一部分 —— 每次部署之后都要再跑一次这个脚本。
#
#   ssh hermes-media
#   cd /RSI/Meristem && git fetch origin && git reset --hard origin/main
#   set -a && . /RSI/meristem-env && set +a            # <-- MERISTEM_VAULT 从这里来
#   bash substrate/deploy-permissions.sh               # <-- 别忘了这一步
#   python3 -m pytest tests/ -q                        # CA-2 会验证它
#
# **不 source env 就跑，本脚本会拒绝并退 2**（见下面的 MERISTEM_VAULT 检查）。
# 写这条是因为作者自己第一次就这么跑了：输出被丢进 /dev/null，
# 于是「脚本没跑成」表现为「CA-2 挂了」，多绕了一圈。
# 拒绝是对的，看不见拒绝才是问题 —— **别把部署脚本的输出丢掉。**
#
# 验收：CA-2 打印 `SECURITY_ASSURANCE=FULL`，且
# tests/test_security_boundaries.py 里三条 organ 隔离断言从 skip 变成 pass。
# 只要其中任何一条还在 skip，本机就**没有**兑现 §15.6 的 P0-a 档。
set -euo pipefail

REPO="${MERISTEM_REPO:-/RSI/Meristem}"
VAULT="${MERISTEM_VAULT:-}"

if [[ -z "$VAULT" ]]; then
    echo "MERISTEM_VAULT 未设置。vault 必须显式给出绝对路径 —— 本脚本不提供" >&2
    echo "任何相对路径缺省（C-65：那个缺省失败时不报错，只是把 vault 定位到错的地方）。" >&2
    exit 2
fi

echo "==> 账户（§15.6 执行身份模型）"
# `worker` **不得有附加组** —— 附加组是它拿到额外读权限的最短路径。
id soil   >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin soil
id worker >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin worker
id soil; id worker

# **每次都核实，不只在创建那一刻。** 上一版只在账户缺席时 useradd，于是一个
# 早已存在、带着附加组的同名 `worker` 会被照单全收 —— 而「worker 无附加组」
# 正是整个隔离论证的地基。**一个只在首次部署时为真的不变量，不是不变量。**
# 幂等 ≠「重跑不报错」，幂等是「重跑之后状态一定对」。
worker_groups="$(id -Gn worker | tr ' ' '\n' | grep -v '^worker$' || true)"
if [ -n "$worker_groups" ]; then
    echo "worker 带有附加组：$(echo "$worker_groups" | tr '\n' ' ')" >&2
    echo "§15.6 要求无附加组 —— 那是它拿到额外读权限的最短路径。拒绝继续。" >&2
    exit 3
fi
worker_shell="$(getent passwd worker | cut -d: -f7)"
case "$worker_shell" in
    */nologin|*/false) ;;
    *) echo "worker 的 shell 是 '$worker_shell'，应为 nologin。拒绝继续。" >&2; exit 3 ;;
esac

echo "==> vault：soil 属主，0500 —— worker 不可读"
# anchor 的隐藏 case 就存在这里。vault 存在的全部理由是「物理上不可见
# 胜过要求 prompt 不要看」；靠 prompt 不看的那一刻，它就已经不是外部锚了。
chown -R soil:soil "$VAULT"
chmod -R u=rX,g=,o= "$VAULT"

echo "==> state/：soil 属主，目录 0700 / 台账 0600 —— worker 不可读不可写"
# organ 能往台账追加，就能伪造一条 accepted_fitness ——
# **凭空制造出 §1.2 判据要的那个事件**，而整个 P0-a 就是拿它判断 H1 的。
mkdir -p "$REPO/state"
chown -R soil:soil "$REPO/state"
chmod 0700 "$REPO/state"
find "$REPO/state" -type f -exec chmod 0600 {} +

echo "==> 权威矩阵里 Soil写=✓ 的文件（CA-2 逐条验这些）"
# 属主按**前缀族/矩阵行**授予，不按记忆里的文件名列表 —— 列举式的边界会漏。
cd "$REPO"
python3 - "$REPO" <<'PY'
import json, os, pathlib, shutil, subprocess, sys
repo = pathlib.Path(sys.argv[1])
matrix = json.loads((repo / "soil" / "authority-matrix.json").read_text(encoding="utf-8"))
for row in matrix["rows"]:
    path = row.get("path")
    if not path or row.get("soil_write") != "✓":
        continue
    target = repo / path
    if not target.exists():
        print(f"    (尚未存在，跳过) {path}")
        continue
    shutil.chown(target, user="soil", group="soil")
    mode = target.stat().st_mode & 0o777
    # CA-2 只要求「无 group/other 写位」，不要求具体模式 —— 按行清掉写位即可。
    os.chmod(target, mode & ~0o022)
    print(f"    soil:soil {oct(target.stat().st_mode & 0o777)} {path}")
PY

echo
echo "==> 自检"
python3 -c "
import sys; sys.path.insert(0, '$REPO')
from substrate import probe_runner
level, reason = probe_runner.isolation_status()
print(f'isolation: {level} -- {reason}')
sys.exit(0 if level == 'enforced' else 1)
"
echo "完成。跑 python3 -m pytest tests/ -q 让 CA-2 与三条隔离断言来验收。"
