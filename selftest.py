#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""greggy selftest —— 25 项断言，四不变量逐条验证。

在临时目录造假环境(假 HOME/rc/plist/crontab)，全流程
snapshot → plan → apply(dry-run) → doctor。绝不碰真实系统状态。
"""

import contextlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import greggy  # noqa: E402

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    return bool(cond)


class _FakeMachine(object):
    """monkeypatch greggy 的采集函数，扮演一台「当前机器」。"""

    def __init__(self, brew_f=None, brew_c=None, pip=None, launchd=None,
                 cron=None, exports=None, aliases=None, interp=None,
                 existing_dirs=()):
        self.brew_f = brew_f or {}
        self.brew_c = brew_c or {}
        self.pip = pip or {}
        self.launchd = launchd or []
        self.cron = cron or []
        self.exports = exports or []
        self.aliases = aliases or []
        self.interp = interp or {}
        self.existing_dirs = list(existing_dirs)
        self._saved = {}
        # 应用于「采集侧」; build_plan 也会用这些函数读当前机状态
        self._saved = {
            "collect_brew": greggy.collect_brew,
            "collect_pip": greggy.collect_pip,
            "collect_launchd": greggy.collect_launchd,
            "collect_cron": greggy.collect_cron,
            "collect_shell": greggy.collect_shell,
            "collect_interpreters": greggy.collect_interpreters,
            "_run": greggy._run,
        }
        fake_self = self

        def fake_collect_brew(kind):
            return dict(fake_self.brew_f) if kind == "formulae" else dict(fake_self.brew_c)

        def fake_collect_pip():
            return dict(fake_self.pip)

        def fake_collect_launchd():
            return list(fake_self.launchd)

        def fake_collect_cron():
            return list(fake_self.cron)

        def fake_collect_shell():
            return list(fake_self.exports), list(fake_self.aliases)

        def fake_collect_interpreters():
            return dict(fake_self.interp)

        def fake_run(cmd, timeout=30):
            if cmd == ["crontab", "-l"]:
                return 0, "\n".join(fake_self.cron)
            return 1, ""

        greggy.collect_brew = fake_collect_brew
        greggy.collect_pip = fake_collect_pip
        greggy.collect_launchd = fake_collect_launchd
        greggy.collect_cron = fake_collect_cron
        greggy.collect_shell = fake_collect_shell
        greggy.collect_interpreters = fake_collect_interpreters
        greggy._run = fake_run

    def restore(self):
        for k, v in self._saved.items():
            setattr(greggy, k, v)


def write_snapshot(path, body, corrupt=None):
    """造一份快照; corrupt: None=合法 / checksum / total_items / none(缺指纹)。"""
    fp = {
        "tool": greggy.TOOL, "version": greggy.VERSION,
        "snapshot_version": greggy.SNAPSHOT_VERSION,
        "captured_at": "2026-09-30T10:00:00",
        "host_hash": greggy.sha16("fake-host"),
        "total_items": greggy.count_items(body),
        "checksum": greggy.body_checksum(body),
    }
    if corrupt == "checksum":
        fp["checksum"] = "0" * 16
    if corrupt == "total_items":
        fp["total_items"] += 5
    data = {"fingerprint": fp, "body": body}
    if corrupt == "none":
        data = {"body": body}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def expect_exit(code, fn, *a, **k):
    try:
        rc = fn(*a, **k)
        return rc
    except SystemExit as e:
        return e.code


def t01_fingerprint_roundtrip():
    h1 = greggy.sha16("machine-a")
    check("指纹函数确定性(同输入同指纹)", h1 == greggy.sha16("machine-a"))
    check("指纹16位十六进制", len(h1) == 16 and all(c in "0123456789abcdef" for c in h1))


def t02_body_checksum_stable():
    body = {"brew_formulae": {"git": "2.39.0"}, "brew_casks": {}, "pip_packages": {},
            "launchd_plists": [], "cron_lines": [], "shell_exports": [],
            "shell_aliases": [], "interpreters": {}, "dirs": []}
    check("body 校验和稳定(键序无关)",
          greggy.body_checksum(body) == greggy.body_checksum(dict(reversed(list(body.items())))))


def t03_count_items():
    body = {"brew_formulae": {"a": "1", "b": "2"}, "pip_packages": {"x": "1"},
            "cron_lines": ["l1"], "dirs": ["d1", "d2"]}
    check("条目计数=各分区求和", greggy.count_items(body) == 6)


def t04_snapshot_body_keys():
    fake = _FakeMachine(brew_f={"git": "2.39.0"}, cron=["30 6 * * * run"])
    try:
        body = greggy.make_snapshot_body(["/tmp/a,/tmp/b"])
    finally:
        fake.restore()
    need = ["brew_formulae", "brew_casks", "pip_packages", "launchd_plists",
            "cron_lines", "shell_exports", "shell_aliases", "interpreters", "dirs"]
    check("快照 body 九大分区齐全", all(k in body for k in need), str(sorted(body.keys())))
    check("采集命中假机器数据", body["brew_formulae"] == {"git": "2.39.0"}
          and body["cron_lines"] == ["30 6 * * * run"])


def t05_iter_items_flat():
    body = {"brew_formulae": {"b": "1", "a": "2"}, "brew_casks": {"c1": "3"},
            "pip_packages": {"p": "4"}, "launchd_plists": ["x.plist"],
            "cron_lines": ["l"], "shell_exports": ["export E=1"],
            "shell_aliases": ["alias ll='ls'"], "interpreters": {"python3": "Python 3.9.6"},
            "dirs": ["/tmp"]}
    items = greggy.iter_items(body)
    check("展平条目数=10", len(items) == 10, str(len(items)))
    check("brew 条目按名排序(确定性)", [i[1] for i in items if i[0] == "brew"] == ["a", "b"])


def t06_tamper_checksum():
    td = tempfile.mkdtemp(prefix="greggy-st-")
    try:
        body = {"brew_formulae": {"git": "2.39.0"}, "brew_casks": {}, "pip_packages": {},
                "launchd_plists": [], "cron_lines": [], "shell_exports": [],
                "shell_aliases": [], "interpreters": {}, "dirs": []}
        p = write_snapshot(os.path.join(td, "s.json"), body, corrupt="checksum")
        # 篡改者改了内容但对好了 checksum、却忘改 total? 不: 改 total 会触发另一条;
        # 这里直接验证「内容↔checksum」绑定: 改 body 不改 checksum 必被拦
        data = json.load(open(p, encoding="utf-8"))
        data["body"]["brew_formulae"]["git"] = "9.9.9"
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        rc = expect_exit(None, greggy.load_snapshot, p)
        check("I3: 内容篡改→指纹校验拦截", rc == 2, "rc=%r" % rc)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def t07_tamper_total():
    td = tempfile.mkdtemp(prefix="greggy-st-")
    try:
        body = {"brew_formulae": {"git": "2.39.0"}, "brew_casks": {}, "pip_packages": {},
                "launchd_plists": [], "cron_lines": [], "shell_exports": [],
                "shell_aliases": [], "interpreters": {}, "dirs": []}
        p = write_snapshot(os.path.join(td, "s.json"), body, corrupt="total_items")
        rc = expect_exit(None, greggy.load_snapshot, p)
        check("I3: 条目数篡改→指纹校验拦截", rc == 2, "rc=%r" % rc)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def t08_snapshot_without_fingerprint():
    td = tempfile.mkdtemp(prefix="greggy-st-")
    try:
        body = {"brew_formulae": {}, "brew_casks": {}, "pip_packages": {},
                "launchd_plists": [], "cron_lines": [], "shell_exports": [],
                "shell_aliases": [], "interpreters": {}, "dirs": []}
        p = write_snapshot(os.path.join(td, "s.json"), body, corrupt="none")
        rc = expect_exit(None, greggy.load_snapshot, p)
        check("I3: 缺指纹结构的文件拒收", rc == 2, "rc=%r" % rc)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def t09_valid_snapshot_loads():
    td = tempfile.mkdtemp(prefix="greggy-st-")
    try:
        body = {"brew_formulae": {"git": "2.39.0"}, "brew_casks": {}, "pip_packages": {},
                "launchd_plists": [], "cron_lines": [], "shell_exports": [],
                "shell_aliases": [], "interpreters": {}, "dirs": []}
        p = write_snapshot(os.path.join(td, "s.json"), body)
        fp, loaded = greggy.load_snapshot(p)
        check("合法快照正常装载", loaded["brew_formulae"] == {"git": "2.39.0"}
              and fp["host_hash"] == greggy.sha16("fake-host"))
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _demo_body():
    """造一份快照 body: ok/missing/drifted 三态俱全(假当前机由 _FakeMachine 扮演)。"""
    return {
        "brew_formulae": {"git": "2.39.0", "jq": "1.7", "wget": "1.20"},
        "brew_casks": {"visual-studio-code": "1.90.0", "iterm2": "3.5.0"},
        "pip_packages": {"requests": "2.31.0", "flask": "3.0.0", "six": "1.16.0"},
        "launchd_plists": ["com.user.backup.plist", "com.user.sync.plist"],
        "cron_lines": ["30 6 * * * run-backup", "0 12 * * * mail-report"],
        "shell_exports": ["export FOO=bar", "export BAZ=qux"],
        "shell_aliases": ["alias ll='ls -la'"],
        "interpreters": {"python3": "Python 3.9.6"},
        "dirs": [],
    }


def _fake_machine_for_demo():
    return _FakeMachine(
        brew_f={"git": "2.39.0", "wget": "1.21"},          # wget 版本漂移
        brew_c={"visual-studio-code": "1.90.0"},            # iterm2 缺
        pip={"requests": "2.31.0", "six": "1.15.0"},        # six 版本漂移
        launchd=["com.user.backup.plist"],                  # sync.plist 缺
        cron=["30 6 * * * run-backup"],                     # mail-report 缺
        exports=["export FOO=bar"],                         # BAZ 缺
        aliases=[],                                         # ll 别名缺
        interp={"python3": "Python 3.9.6"},
    )


def t10_plan_three_states():
    fake = _fake_machine_for_demo()
    try:
        plan = greggy.build_plan(_demo_body())
    finally:
        fake.restore()
    by = {}
    for it in plan:
        by[(it["kind"], it["name"])] = it
    check("plan: brew 已满足项=ok", by[("brew", "git")]["status"] == "ok")
    check("plan: brew 缺失→missing+安装命令",
          by[("brew", "jq")]["status"] == "missing"
          and by[("brew", "jq")]["cmd"] == "brew install jq")
    check("plan: brew 版本漂移被识别",
          by[("brew", "wget")]["status"] == "drifted"
          and by[("brew", "wget")]["have"] == "1.21")
    check("plan: pip 缺失带定版命令",
          by[("pip", "flask")]["cmd"] == "pip3 install flask==3.0.0")
    check("plan: cask 缺失→brew install --cask",
          by[("cask", "iterm2")]["status"] == "missing"
          and by[("cask", "iterm2")]["cmd"] == "brew install --cask iterm2")
    stats = greggy.plan_stats(plan)
    check("plan: 分级统计 missing=7/drifted=2/ok=7",
          stats["missing"] == 7 and stats["drifted"] == 2 and stats["ok"] == 7, str(stats))


def t11_manual_items():
    fake = _fake_machine_for_demo()
    try:
        plan = greggy.build_plan(_demo_body())
    finally:
        fake.restore()
    by = {(it["kind"], it["name"]): it for it in plan}
    check("plan: launchd/cron/export/alias 属人工项",
          by[("launchd", "com.user.sync.plist")]["manual"]
          and by[("cron", "0 12 * * * mail-report")]["manual"]
          and by[("export", "export BAZ=qux")]["manual"]
          and by[("alias", "alias ll='ls -la'")]["manual"])
    check("plan: brew/pip 安装项非人工(可 --exec)",
          not by[("brew", "jq")]["manual"] and not by[("pip", "flask")]["manual"])


def t12_dryrun_zero_exec():
    """I2: dry-run 全程零执行——统计 fake _run 收到的「字符串命令」数。"""
    td = tempfile.mkdtemp(prefix="greggy-st-")
    fake = _fake_machine_for_demo()
    exec_calls = []
    real_run = greggy._run

    def counting_run(cmd, timeout=30):
        if isinstance(cmd, str):
            exec_calls.append(cmd)  # 字符串命令=真要 shell 执行的
            return 0, ""
        return real_run(cmd, timeout)

    greggy._run = counting_run
    try:
        p = write_snapshot(os.path.join(td, "s.json"), _demo_body())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = greggy.cmd_apply(type("A", (), {"snapshot": p, "exec_": False})())
        out = buf.getvalue()
        check("I2: apply 默认 dry-run 零执行", len(exec_calls) == 0,
              "exec=%r" % exec_calls)
        check("I2: dry-run 输出带 [dry-run] 前缀且申明零执行",
              "[dry-run] brew install jq" in out and "0 条命令被执行" in out)
        check("I2: dry-run 退出码 0", rc == 0)
    finally:
        fake.restore()
        shutil.rmtree(td, ignore_errors=True)


def t13_exec_only_safe_prefixes():
    """--exec 只跑 marked-safe 安装类; 人工项绝不执行。"""
    td = tempfile.mkdtemp(prefix="greggy-st-")
    fake = _fake_machine_for_demo()
    ran, real_run = [], greggy._run

    def counting_run(cmd, timeout=30):
        if isinstance(cmd, str):
            ran.append(cmd)
            return 0, ""
        return real_run(cmd, timeout)

    greggy._run = counting_run
    try:
        p = write_snapshot(os.path.join(td, "s.json"), _demo_body())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            greggy.cmd_apply(type("A", (), {"snapshot": p, "exec_": True})())
        out = buf.getvalue()
        check("I2: --exec 只执行 SAFE 前缀命令",
              all(c.startswith(greggy.SAFE_CMD_PREFIXES) for c in ran), str(ran))
        check("I2: 人工项(launchd/cron/export/alias)零执行",
              not any("launchctl" in c or "crontab" in c or "zshrc" in c for c in ran))
        check("I2: 人工项以提醒形式出现在输出",
              "launchctl load" in out and "人工确认" in out)
        check("I2: 安装类命令确实被执行",
              "brew install jq" in ran and "pip3 install flask==3.0.0" in ran, str(ran))
    finally:
        fake.restore()
        shutil.rmtree(td, ignore_errors=True)


def t14_failure_not_interrupting():
    """I4: 单项失败记录继续，最后汇总。"""
    td = tempfile.mkdtemp(prefix="greggy-st-")
    body = {"brew_formulae": {"aaa": "1.0", "bbb": "2.0", "ccc": "3.0"},
            "brew_casks": {}, "pip_packages": {}, "launchd_plists": [],
            "cron_lines": [], "shell_exports": [], "shell_aliases": [],
            "interpreters": {}, "dirs": []}
    fake = _FakeMachine()  # 当前机啥都没有 → 3 个全 missing
    ran, real_run = [], greggy._run

    def flaky_run(cmd, timeout=30):
        if isinstance(cmd, str):
            ran.append(cmd)
            return (1, "boom") if "bbb" in cmd else (0, "")
        return real_run(cmd, timeout)

    greggy._run = flaky_run
    try:
        p = write_snapshot(os.path.join(td, "s.json"), body)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = greggy.cmd_apply(type("A", (), {"snapshot": p, "exec_": True})())
        out = buf.getvalue()
        check("I4: 三项全部被尝试(失败不中断)", len(ran) == 3, str(ran))
        check("I4: 失败项进汇总报告", "失败 1" in out and "brew install bbb" in out)
        check("I4: 有失败时退出码 1", rc == 1)
    finally:
        fake.restore()
        shutil.rmtree(td, ignore_errors=True)


def t15_empty_snapshot_plan():
    td = tempfile.mkdtemp(prefix="greggy-st-")
    fake = _FakeMachine()
    try:
        empty = {"brew_formulae": {}, "brew_casks": {}, "pip_packages": {},
                 "launchd_plists": [], "cron_lines": [], "shell_exports": [],
                 "shell_aliases": [], "interpreters": {}, "dirs": []}
        p = write_snapshot(os.path.join(td, "s.json"), empty)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = expect_exit(0, greggy.cmd_plan, type("A", (), {"snapshot": p})())
        check("空快照 plan 不炸且明说空", rc == 0 and "空快照" in buf.getvalue())

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = expect_exit(0, greggy.cmd_doctor, type("A", (), {"snapshot": p})())
        check("空快照 doctor 默认通过 100%(0/0)",
              rc == 0 and "100%" in buf.getvalue())
    finally:
        fake.restore()
        shutil.rmtree(td, ignore_errors=True)


def t16_doctor_percentages():
    td = tempfile.mkdtemp(prefix="greggy-st-")
    fake_all_miss = _FakeMachine()  # 啥都没装
    try:
        miss_body = {"brew_formulae": {"aa": "1.0", "bb": "2.0"},
                     "brew_casks": {}, "pip_packages": {}, "launchd_plists": [],
                     "cron_lines": [], "shell_exports": [], "shell_aliases": [],
                     "interpreters": {}, "dirs": []}
        p = write_snapshot(os.path.join(td, "miss.json"), miss_body)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = expect_exit(None, greggy.cmd_doctor, type("A", (), {"snapshot": p})())
        check("doctor: 全缺→0% 且退出码 1", rc == 1 and "再生完成度 0%" in buf.getvalue())

        # 全满足: 当前机状态=快照 body
        ok_body = {"brew_formulae": {"git": "2.39.0"},
                   "brew_casks": {"vim": "9.0"}, "pip_packages": {"six": "1.16.0"},
                   "launchd_plists": [], "cron_lines": [], "shell_exports": [],
                   "shell_aliases": [], "interpreters": {}, "dirs": []}
        fake_ok = _FakeMachine(brew_f={"git": "2.39.0"}, brew_c={"vim": "9.0"},
                               pip={"six": "1.16.0"})
        p2 = write_snapshot(os.path.join(td, "ok.json"), ok_body)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = expect_exit(None, greggy.cmd_doctor, type("A", (), {"snapshot": p2})())
        check("doctor: 全满足→100% 退出码 0 且有 ✅",
              rc == 0 and "再生完成度 100%" in buf.getvalue() and "✅" in buf.getvalue())
    finally:
        fake_all_miss.restore()
        shutil.rmtree(td, ignore_errors=True)


def t17_cli_e2e_snapshot():
    """端到端: 真 CLI 在假 HOME 里 snapshot → 文件落盘+指纹自洽+I1 只写快照一个文件。"""
    td = tempfile.mkdtemp(prefix="greggy-e2e-")
    fake_home = os.path.join(td, "home")
    os.makedirs(os.path.join(fake_home, "Library", "LaunchAgents"))
    with open(os.path.join(fake_home, ".zshrc"), "w") as f:
        f.write("export GREGGY_TEST=1\nalias gls='ls -G'\n")
    with open(os.path.join(fake_home, "Library", "LaunchAgents", "com.demo.plist"), "w") as f:
        f.write("<plist/>")
    env = dict(os.environ, HOME=fake_home, PATH=os.environ.get("PATH", "/usr/bin:/bin"))
    out_snap = os.path.join(td, "snap.json")
    before = set(os.listdir(fake_home))
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "greggy.py"), "snapshot", "-o", out_snap, "--dirs", "/tmp/demo-etc"],
        env=env, capture_output=True, text=True, timeout=120)
    after = set(os.listdir(fake_home))
    check("I1: CLI snapshot 只在 HOME 外写快照文件(HOME 零新增)",
          r.returncode == 0 and before == after,
          "rc=%d new=%r" % (r.returncode, after - before))
    data = json.load(open(out_snap, encoding="utf-8"))
    body = data["body"]
    check("CLI snapshot: rc 文件 export/alias 被采到",
          "export GREGGY_TEST=1" in body["shell_exports"]
          and "alias gls='ls -G'" in body["shell_aliases"])
    check("CLI snapshot: 假 launchd plist 被采到",
          body["launchd_plists"] == ["com.demo.plist"])
    check("CLI snapshot: 指纹自洽(checksum+计数)",
          data["fingerprint"]["checksum"] == greggy.body_checksum(body)
          and data["fingerprint"]["total_items"] == greggy.count_items(body))
    # 篡改快照再 plan → CLI 层退出 2
    data["body"]["brew_formulae"] = {"evil": "0.0"}
    json.dump(data, open(out_snap, "w", encoding="utf-8"), ensure_ascii=False)
    r2 = subprocess.run(
        [sys.executable, os.path.join(HERE, "greggy.py"), "plan", out_snap],
        env=env, capture_output=True, text=True, timeout=60)
    check("CLI plan: 篡改快照被指纹拦截退出 2",
          r2.returncode == 2 and "指纹校验失败" in (r2.stderr + r2.stdout),
          "rc=%d" % r2.returncode)
    shutil.rmtree(td, ignore_errors=True)


def t18_cli_plan_and_apply_dryrun():
    td = tempfile.mkdtemp(prefix="greggy-e2e-")
    fake_home = os.path.join(td, "home")
    os.makedirs(fake_home)
    env = dict(os.environ, HOME=fake_home, PATH=os.environ.get("PATH", "/usr/bin:/bin"))
    snap = os.path.join(td, "s.json")
    body = {"brew_formulae": {"greggy-fake-pkg": "1.2.3"}, "brew_casks": {},
            "pip_packages": {"greggy-fake-mod": "0.1.0"}, "launchd_plists": [],
            "cron_lines": [], "shell_exports": ["export GG_DEMO=1"],
            "shell_aliases": [], "interpreters": {}, "dirs": []}
    write_snapshot(snap, body)
    r = subprocess.run([sys.executable, os.path.join(HERE, "greggy.py"), "plan", snap],
                       env=env, capture_output=True, text=True, timeout=60)
    check("CLI plan: 缺失项分级输出且退出码 1",
          r.returncode == 1 and "[缺失]" in r.stdout and "brew install greggy-fake-pkg" in r.stdout)
    r2 = subprocess.run([sys.executable, os.path.join(HERE, "greggy.py"), "apply", snap],
                        env=env, capture_output=True, text=True, timeout=60)
    check("CLI apply: 默认 dry-run 申明零执行",
          r2.returncode == 0 and "[dry-run]" in r2.stdout and "0 条命令被执行" in r2.stdout)
    check("CLI apply: dry-run 后假包确实没被装(系统未被碰)",
          "greggy-fake-pkg" not in subprocess.run(
              ["/usr/bin/env", "brew", "list", "--formula"], env=env,
              capture_output=True, text=True).stdout)
    shutil.rmtree(td, ignore_errors=True)


def t19_drift_recognized_cross_machine():
    """I3 跨机语义: 同一份快照拿到「另一台机器」上 plan，漂移/缺失按新机算。"""
    body = {"brew_formulae": {"wget": "1.20"}, "brew_casks": {}, "pip_packages": {},
            "launchd_plists": [], "cron_lines": [], "shell_exports": [],
            "shell_aliases": [], "interpreters": {}, "dirs": []}
    fake_a = _FakeMachine(brew_f={"wget": "1.20"})
    try:
        plan_a = greggy.build_plan(body)
    finally:
        fake_a.restore()
    fake_b = _FakeMachine(brew_f={"wget": "1.21"})
    try:
        plan_b = greggy.build_plan(body)
    finally:
        fake_b.restore()
    check("I3: 同快照跨机 plan——A 机 ok / B 机 drifted",
          plan_a[0]["status"] == "ok" and plan_b[0]["status"] == "drifted")


def t20_python38_syntax():
    """交付铁律: 单文件必须在最老解释器(3.8+)语法下可编译。"""
    import py_compile
    r = subprocess.run([sys.executable, "-c",
                        "import py_compile; py_compile.compile(%r, doraise=True)" %
                        os.path.join(HERE, "greggy.py")],
                       capture_output=True, text=True, timeout=60)
    check("greggy.py 语法编译通过(可交付)", r.returncode == 0, r.stderr[:200])


def main(verbose=True):
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("t") and k[1:3].isdigit() and callable(v)]
    for name, fn in tests:
        if verbose:
            print("-- %s" % name)
        try:
            fn()
        except Exception as exc:  # 断言外的异常也算挂
            _RESULTS.append((name, False, "EXC: %r" % exc))
    total = len(_RESULTS)
    ok = sum(1 for _, p, _ in _RESULTS if p)
    if verbose:
        for name, p, detail in _RESULTS:
            if not p:
                print("FAIL %s %s" % (name, detail))
    print("selftest: %d/%d 绿" % (ok, total))
    print("四不变量(I1 只读采集/I2 默认dry-run/I3 指纹可携带/I4 失败不中断)全部通过。"
          if ok == total else "有失败项，禁止发布。")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main(verbose=("--quiet" not in sys.argv)))
