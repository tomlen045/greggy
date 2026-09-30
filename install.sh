#!/bin/sh
# greggy 安装脚本 —— 拉单文件到 ~/.local/bin（或当前目录）
set -e
DEST="${GREGGY_DEST:-$HOME/.local/bin}"
mkdir -p "$DEST"
BASE_URLS=(
  "https://gitee.com/tomlen/greggy/raw/main/greggy.py"
  "https://raw.githubusercontent.com/tomlen045/greggy/main/greggy.py"
  "https://gcore.jsdelivr.net/gh/tomlen045/greggy@main/greggy.py"
)
ok=""
for u in "${BASE_URLS[@]}"; do
  if curl -fsSL --max-time 30 "$u" -o "$DEST/greggy.py.tmp" 2>/dev/null; then
    # jsDelivr/镜像 404 页面伪装成功拦截：「Couldn't find the requested file」是 404 文案不是脚本内容
    if grep -q "Couldn't find the requested file" "$DEST/greggy.py.tmp" 2>/dev/null; then
      rm -f "$DEST/greggy.py.tmp"
      continue
    fi
    # 真脚本必有 argparse 子命令入口与工具标语
    if grep -q "add_subparsers" "$DEST/greggy.py.tmp" && grep -q "机器环境再生卡" "$DEST/greggy.py.tmp"; then
      ok="$u"
      break
    fi
  fi
  rm -f "$DEST/greggy.py.tmp"
done
if [ -z "$ok" ]; then
  echo "✘ 三个镜像都拉取失败，请手动下载 greggy.py" >&2
  exit 1
fi
mv "$DEST/greggy.py.tmp" "$DEST/greggy.py"
chmod +x "$DEST/greggy.py"
echo "✔ 已装到 $DEST/greggy.py（来源: $ok）"
"$DEST/greggy.py" selftest -q | tail -2
echo "上手: greggy.py snapshot -o greggy-snapshot.json"
