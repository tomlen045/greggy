# greggy

[中文](README.md) | [English](README_EN.md)

**机器环境再生卡** —— 换机/重装/灾难恢复时「装到一半发现漏了个 brew 包、忘了个 launchd 任务、环境变量对不上」，全靠回忆重建机器的日子该结束了。
bakcheck 验备份产物能不能恢复（[bakcheck](https://github.com/tomlen045/bakcheck)），**greggy 管机器环境本身的再生**：给这台机器拍一张环境快照，哪天机器炸了，一条命令把快照变成可执行的重建清单，逐项打勾直到「再生」完毕。

四个命令：**snapshot**（拍快照）→ **plan**（对当前机出分级重建计划）→ **apply**（打勾执行，默认 dry-run）→ **doctor**（体检出再生完成度）。

[![tests](https://img.shields.io/badge/self--tests-44%2F44-green)]() [![deps](https://img.shields.io/badge/deps-zero-yellow)]() [![license](https://img.shields.io/badge/license-MIT-blue)]()

---

## 解决什么问题

* 新机器/重装后想不起来装过啥 → **snapshot 一张 JSON 快照**：brew/cask/pip 包清单（带版本）、launchd 用户任务、crontab、shell rc 里的 export/alias 行、python/node 等解释器版本、常用目录清单——机器的全部家当，一份文件带走
* 快照是重建的「图纸」，图纸被改过就完蛋 → **SHA256 指纹绑定内容+条目数**，plan/apply/doctor 读取时先验指纹，手改一个字符都直接拒绝（exit 2）
* 新机器拿来不敢让它乱跑命令 → **apply 默认 dry-run**，只打印将执行的命令一个不跑；`--exec` 也只跑 brew/pip 安装类，launchd/cron/shell 配置类一律拦下提醒人工确认
* 装到一半某个包装不上 → **失败不中断**：单项失败记录在案继续下一项，最后汇总报告
* 重建完到底齐没齐 → **doctor 逐项体检**打 ✅/❌/⏭(人工项)，末尾给「再生完成度 NN%」，100% 才算再生完成
* 版本对不上最隐蔽 → **漂移检测**：快照里 wget 1.21 / 当前 1.20 这种版本漂移单独标 `[漂移]`，跟 `[缺失]` 分开列

## 安装

```bash
# 方式一：一键脚本（Gitee → GitHub → jsDelivr 三镜像自动切换）
curl -fsSL https://gitee.com/tomlen/greggy/raw/main/install.sh | sh

# 方式二：直接拉单文件（仅标准库，Python 3.8+）
curl -fsSLO https://gitee.com/tomlen/greggy/raw/main/greggy.py
```

## 30 秒上手

```bash
# 1) 旧机器上拍快照（只读采集，不碰系统任何文件）
python3 greggy.py snapshot -o greggy-snapshot.json

# 2) 快照拷到新机器（U盘/网盘随意，它自包含+指纹校验），出分级重建计划
python3 greggy.py plan greggy-snapshot.json

# 3) 先看会跑什么（默认 dry-run，零执行）
python3 greggy.py apply greggy-snapshot.json

# 4) 确认无误真跑安装类；launchd/cron/shell 项会列出来等你手工做
python3 greggy.py apply greggy-snapshot.json --exec

# 5) 重建完体检，看再生完成度
python3 greggy.py doctor greggy-snapshot.json
```

## 快照里有什么

| 分区 | 内容 | 重建方式 |
|---|---|---|
| brew_formulae / brew_casks | CLI 包 / 图形应用（带版本） | `brew install`，可 --exec |
| pip_packages | Python 包定版清单 | `pip3 install x==v`，可 --exec |
| launchd_plists | 用户级 LaunchAgents 任务 | 打印 `launchctl load` 提醒人工 |
| cron_lines | crontab 全部行 | 打印提醒人工加回 |
| shell_exports / shell_aliases | rc 文件里的 export/alias 行 | 打印提醒人工写入 |
| interpreters | python3/node/go 等版本 | 版本不一致时提醒人工核对 |
| dirs | 常用目录清单 | `mkdir -p`，可 --exec |

快照顶部带指纹：采集时间 + 机器名哈希 + 总条目数 + 内容 SHA256。人类可读 JSON，出问题肉眼也能查。

## 四不变量

1. **只读采集** —— snapshot/doctor 绝不写系统任何文件（除自身的快照输出文件）
2. **apply 默认 dry-run** —— 不显式 `--exec`，一条命令都不执行
3. **快照可携带** —— JSON 自包含 + 指纹校验，跨机可用；被篡改的快照直接拒收
4. **失败不中断** —— apply 单项失败记录在案继续下一项，最后汇总报告

## 设计边界（说在前面）

* greggy 管**声明式清单与打勾重建**：装什么包、起哪些服务、设什么变量。**不管数据备份**——文件/数据库的备份验证是 [bakcheck](https://github.com/tomlen045/bakcheck) 的活，两个配合用才是完整灾备
* `brew upgrade` 只在版本漂移时出现在计划里；brew 采集不到版本号的包记 `?`，只判存在性不判版本
* `--exec` 是显式信任：跑的是 brew/pip 安装类命令，系统级改动（launchd/cron/shell）永远人工来
* pip 清单来自默认 pip 环境；多 venv 场景请分别对 venv 拍快照

## 自测

```bash
python3 greggy.py selftest   # 44/44 绿（20 组用例，四不变量逐条验证）
```

---

## 工具箱叙事

cronguard 盯任务跑没跑 → logwhisperer 考古日志 → debugkit 立案修复 → porteye 查暴露面 → capguard 管磁盘容量 → bakcheck 验备份 → cfgdrift 抓配置漂移 → cutcheck 检出片 → **greggy 管机器再生**——第九块拼图，机器炸了也能原地满血。

## License

MIT
