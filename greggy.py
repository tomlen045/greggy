#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""greggy v1.0.0 —— 机器环境再生卡

给这台机器拍一张「环境快照」；哪天机器炸了/换新机，一条命令把快照变成
可执行的重建清单——包管理器装什么、服务起哪些、环境变量设什么、shell
配置改哪些，逐项打勾直到机器「再生」完毕。

四不变量:
  I1 只读采集   snapshot/doctor 绝不写系统任何文件(除自身快照输出)
  I2 apply 默认 dry-run，不显式 --exec 一个命令都不执行
  I3 快照可携带  JSON 自包含 + 指纹校验，跨机可用
  I4 失败不中断  apply 单项失败记录继续，最后汇总报告

仅标准库，Python 3.8+。
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

VERSION = "1.0.0"
TOOL = "greggy"
SNAPSHOT_VERSION = 1

RC_FILES = [".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".profile"]
INTERPRETERS = ["python3", "python", "node", "npm", "go", "rustc", "git"]

# apply --exec 只允许跑这些前缀的「安装类」命令(其余一律人工确认)
SAFE_CMD_PREFIXES = ("brew install ", "brew upgrade ", "pip3 install ", "mkdir -p ")


# ---------------------------------------------------------------- helpers

def _run(cmd, timeout=30):
    """跑一条命令，返回 (rc, stdout)。环境缺命令/任何异常都吞掉返回空。"""
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "")
    except Exception:
        return 1, ""


def _which(name):
    return shutil.which(name)


def sha16(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def eprint(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- collect

def collect_brew(kind):
    """kind: formulae / casks → {name: version}，brew 不存在返回 {}"""
    out = {}
    if not _which("brew"):
        return out
    flag = "--formula" if kind == "formulae" else "--cask"
    rc, text = _run(["brew", "list", flag, "--versions"])
    if rc != 0:
        rc, text = _run(["brew", "list", flag, "-1"])
        if rc != 0:
            return out
        return {ln.strip(): "?" for ln in text.splitlines() if ln.strip()}
    for ln in text.splitlines():
        parts = ln.strip().split(None, 1)
        if parts:
            out[parts[0]] = parts[1] if len(parts) > 1 else "?"
    return out


def collect_pip():
    out = {}
    for pcmd in ("pip3", "pip"):
        if not _which(pcmd):
            continue
        rc, text = _run([pcmd, "list", "--format=freeze"])
        if rc != 0:
            continue
        for ln in text.splitlines():
            if "==" in ln:
                name, ver = ln.split("==", 1)
                name = name.strip()
                if name:
                    out[name] = ver.strip()
        break
    return out


def collect_launchd():
    home = os.environ.get("HOME", os.path.expanduser("~"))
    pat = os.path.join(home, "Library", "LaunchAgents", "*.plist")
    return sorted(os.path.basename(p) for p in glob.glob(pat))


def collect_cron():
    rc, text = _run(["crontab", "-l"])
    if rc != 0:
        return []
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def collect_shell():
    home = os.environ.get("HOME", os.path.expanduser("~"))
    exports, aliases = [], []
    for name in RC_FILES:
        path = os.path.join(home, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith("export ") and "=" in line:
                        if line not in exports:
                            exports.append(line)
                    elif line.startswith("alias ") and "=" in line:
                        if line not in aliases:
                            aliases.append(line)
        except Exception:
            continue
    return exports, aliases


def collect_interpreters():
    out = {}
    for cmd in INTERPRETERS:
        if not _which(cmd):
            continue
        rc, text = _run([cmd, "--version"])
        if rc != 0:
            rc, text = _run([cmd, "-V"])
        first = (text.strip().splitlines() or [""])[0]
        if first:
            out[cmd] = first
    return out


def collect_dirs(dirs_arg):
    out = []
    for d in dirs_arg or []:
        d = d.strip()
        if d:
            out.append(d)
    return out


def make_snapshot_body(dirs_arg):
    exports, aliases = collect_shell()
    return {
        "brew_formulae": collect_brew("formulae"),
        "brew_casks": collect_brew("casks"),
        "pip_packages": collect_pip(),
        "launchd_plists": collect_launchd(),
        "cron_lines": collect_cron(),
        "shell_exports": exports,
        "shell_aliases": aliases,
        "interpreters": collect_interpreters(),
        "dirs": collect_dirs(dirs_arg),
    }


def count_items(body):
    return sum(len(v) for v in body.values())


def body_checksum(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def iter_items(body):
    """把 body 展平成 (kind, name, want) 三元组列表。"""
    items = []
    for name in sorted(body.get("brew_formulae", {})):
        items.append(("brew", name, body["brew_formulae"][name]))
    for name in sorted(body.get("brew_casks", {})):
        items.append(("cask", name, body["brew_casks"][name]))
    for name in sorted(body.get("pip_packages", {})):
        items.append(("pip", name, body["pip_packages"][name]))
    for fn in body.get("launchd_plists", []):
        items.append(("launchd", fn, fn))
    for ln in body.get("cron_lines", []):
        items.append(("cron", ln, ln))
    for ln in body.get("shell_exports", []):
        items.append(("export", ln, ln))
    for ln in body.get("shell_aliases", []):
        items.append(("alias", ln, ln))
    for cmd in sorted(body.get("interpreters", {})):
        items.append(("interp", cmd, body["interpreters"][cmd]))
    for d in body.get("dirs", []):
        items.append(("dir", d, d))
    return items


# ---------------------------------------------------------------- diff/plan

MANUAL_KINDS = {"launchd", "cron", "export", "alias"}


def build_plan(body):
    """快照 body vs 当前机 → 计划条目列表(纯函数，selftest 直接测)。"""
    plan = []
    cur_brew_f = collect_brew("formulae")
    cur_brew_c = collect_brew("casks")
    cur_pip = collect_pip()
    cur_launchd = set(collect_launchd())
    rc, text = _run(["crontab", "-l"])
    cur_cron = set(ln.strip() for ln in text.splitlines() if ln.strip()) if rc == 0 else set()
    cur_exports, cur_aliases = collect_shell()
    cur_exports, cur_aliases = set(cur_exports), set(cur_aliases)
    cur_interp = collect_interpreters()
    home = os.environ.get("HOME", os.path.expanduser("~"))

    for kind, name, want in iter_items(body):
        item = {"kind": kind, "name": name, "want": want,
                "have": None, "status": "ok", "cmd": "", "manual": kind in MANUAL_KINDS}
        if kind == "brew":
            have = cur_brew_f.get(name)
            if have is None:
                item.update(status="missing", cmd="brew install %s" % name)
            elif want not in ("?",) and have.split(",")[0] != want:
                item.update(status="drifted", have=have,
                            cmd="brew upgrade %s" % name)
            else:
                item.update(have=have)
        elif kind == "cask":
            have = cur_brew_c.get(name)
            if have is None:
                item.update(status="missing", cmd="brew install --cask %s" % name)
            elif want not in ("?",) and have.split(",")[0] != want:
                item.update(status="drifted", have=have,
                            cmd="brew upgrade --cask %s" % name)
            else:
                item.update(have=have)
        elif kind == "pip":
            have = cur_pip.get(name)
            if have is None:
                item.update(status="missing", cmd="pip3 install %s==%s" % (name, want))
            elif have != want:
                item.update(status="drifted", have=have,
                            cmd="pip3 install %s==%s" % (name, want))
            else:
                item.update(have=have)
        elif kind == "launchd":
            if name in cur_launchd:
                item.update(have=name)
            else:
                item.update(status="missing",
                            cmd="launchctl load %s" % os.path.join(home, "Library", "LaunchAgents", name))
        elif kind == "cron":
            if name.strip() in cur_cron:
                item.update(have=name)
            else:
                item.update(status="missing", cmd="(crontab -e 加入) %s" % name)
        elif kind == "export":
            if name in cur_exports:
                item.update(have=name)
            else:
                item.update(status="missing", cmd="echo '%s' >> ~/.zshrc" % name)
        elif kind == "alias":
            if name in cur_aliases:
                item.update(have=name)
            else:
                item.update(status="missing", cmd="echo '%s' >> ~/.zshrc" % name)
        elif kind == "interp":
            have = cur_interp.get(name)
            if have is None:
                item.update(status="missing", cmd="(人工安装解释器) %s" % name)
            elif have != want:
                item.update(status="drifted", have=have, cmd="(人工核对版本) %s" % name)
            else:
                item.update(have=have)
        elif kind == "dir":
            if os.path.isdir(os.path.expanduser(name)):
                item.update(have=name)
            else:
                item.update(status="missing",
                            cmd="mkdir -p %s" % name, manual=False)
        plan.append(item)
    return plan


def plan_stats(plan):
    st = {"missing": 0, "drifted": 0, "ok": 0}
    for it in plan:
        st[it["status"]] = st.get(it["status"], 0) + 1
    return st


def load_snapshot(path):
    """读快照并校验指纹(I3)。失败抛 SystemExit(2)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        eprint("✘ 快照读不了: %s (%s)" % (path, exc))
        raise SystemExit(2)
    try:
        fp = data["fingerprint"]
        body = data["body"]
    except KeyError:
        eprint("✘ 不是 greggy 快照: 缺 fingerprint/body 结构")
        raise SystemExit(2)
    if fp.get("checksum") != body_checksum(body):
        eprint("✘ 指纹校验失败: 快照内容与指纹不符——文件被篡改或损坏，拒绝使用")
        raise SystemExit(2)
    if int(fp.get("total_items", -1)) != count_items(body):
        eprint("✘ 指纹校验失败: 条目总数与指纹不符——文件被篡改或损坏，拒绝使用")
        raise SystemExit(2)
    return fp, body


# ---------------------------------------------------------------- commands

def cmd_snapshot(args):
    body = make_snapshot_body((args.dirs or "").split(",") if args.dirs else [])
    fp = {
        "tool": TOOL,
        "version": VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "captured_at": now_iso(),
        "host_hash": sha16(_run(["hostname"])[1].strip() or os.uname().nodename),
        "total_items": count_items(body),
        "checksum": body_checksum(body),
    }
    out = {"fingerprint": fp, "body": body}
    text = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("greggy v%s 快照完成 → %s" % (VERSION, args.output))
    print("  采集时间: %s   机器指纹: %s   共 %d 项" %
          (fp["captured_at"], fp["host_hash"], fp["total_items"]))
    for label, key in [("brew", "brew_formulae"), ("cask", "brew_casks"),
                       ("pip", "pip_packages"), ("launchd", "launchd_plists"),
                       ("cron", "cron_lines"), ("export", "shell_exports"),
                       ("alias", "shell_aliases"), ("interp", "interpreters"),
                       ("dir", "dirs")]:
        print("  %-8s %d 项" % (label, len(body.get(key, []))))
    print("I1 只读采集: 除本快照文件外未写任何系统文件。")
    return 0


def cmd_plan(args):
    fp, body = load_snapshot(args.snapshot)
    plan = build_plan(body)
    st = plan_stats(plan)
    total = len(plan)
    print("greggy v%s 重建计划 — 快照 %s (机器 %s)" %
          (VERSION, fp.get("captured_at", "?"), fp.get("host_hash", "?")))
    print("共 %d 项: missing %d / drifted %d / ok %d" %
          (total, st["missing"], st["drifted"], st["ok"]))
    if total == 0:
        print("空快照: 没有采集到任何可重建项，无需重建。")
        return 0
    order = {"missing": 0, "drifted": 1, "ok": 2}
    for it in sorted(plan, key=lambda x: (order[x["status"]], x["kind"], x["name"])):
        tag = {"missing": "[缺失]", "drifted": "[漂移]", "ok": "[已满足]"}[it["status"]]
        line = "%s %-8s %s" % (tag, it["kind"], it["name"])
        if it["status"] == "drifted":
            line += "  (快照 %s / 当前 %s)" % (it["want"], it["have"])
        if it["cmd"]:
            line += "\n           → %s%s" % (it["cmd"], "   # 人工确认" if it["manual"] else "")
        print(line)
    if st["missing"] or st["drifted"]:
        print("下一步: python3 %s apply %s --dry-run  (默认 dry-run，--exec 才真跑)" %
              (TOOL, args.snapshot))
        return 1
    print("全部满足，这台机器不缺东西。")
    return 0


def cmd_apply(args):
    fp, body = load_snapshot(args.snapshot)
    plan = build_plan(body)
    todo = [it for it in plan if it["status"] in ("missing", "drifted")]
    if not todo:
        print("greggy v%s — 没有需要重建的项，机器已与快照一致。" % VERSION)
        return 0
    mode = "DRY-RUN(不执行)" if not args.exec_ else "EXEC(--exec)"
    print("greggy v%s apply — %s，共 %d 项待处理" % (VERSION, mode, len(todo)))
    done, failed, manual = [], [], []
    for it in todo:
        cmd = it["cmd"]
        auto = (not it["manual"]) and cmd.startswith(SAFE_CMD_PREFIXES)
        if not args.exec_:
            print("  [dry-run] %s" % cmd)
            continue
        if auto:
            print("  [exec] %s" % cmd)
            rc, out = _run(cmd, timeout=600)
            if rc == 0:
                done.append(cmd)
                print("    ✔ 完成")
            else:
                # I4: 单项失败记录继续
                failed.append((cmd, rc))
                print("    ✘ 失败(rc=%d)，继续下一项" % rc)
        else:
            manual.append(cmd)
            print("  [⚠ 人工确认] %s" % cmd)
    if not args.exec_:
        print("dry-run 结束: 0 条命令被执行(I2)。确认无误后加 --exec 真跑。")
        return 0
    print("---- apply 汇总 ----")
    print("成功 %d / 失败 %d / 人工项 %d" % (len(done), len(failed), len(manual)))
    for cmd, rc in failed:
        print("  失败: %s (rc=%d)" % (cmd, rc))
    if failed:
        return 1
    return 0


def cmd_doctor(args):
    fp, body = load_snapshot(args.snapshot)
    plan = build_plan(body)
    total = len(plan)
    ok = sum(1 for it in plan if it["status"] == "ok")
    print("greggy v%s 再生体检 — 对照快照 %s (机器 %s)" %
          (VERSION, fp.get("captured_at", "?"), fp.get("host_hash", "?")))
    if total == 0:
        print("空快照: 没有可体检项，默认通过。")
        print("再生完成度 100% (0/0)")
        return 0
    for it in sorted(plan, key=lambda x: (x["kind"], x["name"])):
        if it["status"] == "ok":
            mark, tail = "✅", ""
        elif it["status"] == "missing" and it["manual"]:
            mark, tail = "⏭", "  (人工: %s)" % it["cmd"]
        else:
            mark, tail = "❌", "  (%s)" % it["cmd"] if it["cmd"] else ""
        print("%s %-8s %s%s" % (mark, it["kind"], it["name"], tail))
    pct = int(ok * 100 // total)
    print("再生完成度 %d%% (%d/%d)%s" %
          (pct, ok, total, " — 再生完成!" if pct == 100 else " — 还差 %d 项" % (total - ok)))
    return 0 if pct == 100 else 1


# ---------------------------------------------------------------- selftest

def cmd_selftest(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import selftest
    return selftest.main(verbose=not args.quiet)


# ---------------------------------------------------------------- cli

def build_parser():
    ap = argparse.ArgumentParser(
        prog=TOOL,
        description="greggy —— 机器环境再生卡: 环境快照 → 重建计划 → 打勾再生 → 完成度体检")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("snapshot", help="采集当前机器环境 → JSON 快照")
    p.add_argument("-o", "--output", default="greggy-snapshot.json")
    p.add_argument("--dirs", default="", help="额外采集的目录清单, 逗号分隔")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("plan", help="快照 vs 当前机 → 分级重建计划")
    p.add_argument("snapshot", nargs="?", default="greggy-snapshot.json")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="执行重建(默认 dry-run, --exec 才真跑)")
    p.add_argument("snapshot", nargs="?", default="greggy-snapshot.json")
    p.add_argument("--dry-run", action="store_true", help="只打印不执行(默认行为)")
    p.add_argument("--exec", dest="exec_", action="store_true",
                   help="真跑安装类命令; launchd/cron/shell 类仍只提醒")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("doctor", help="重建后体检 + 再生完成度")
    p.add_argument("snapshot", nargs="?", default="greggy-snapshot.json")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("selftest", help="内置自测(不变量逐条验证)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_selftest)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
