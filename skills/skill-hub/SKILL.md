---
name: skill-hub
description: >
  本地 skill 管理系统（网页 http://127.0.0.1:3458）。Use when the user wants to
  browse, search, create, edit, enable/disable, copy or archive Claude Code skills,
  slash commands or agent definitions — "打开 skill 管理", "skill hub", "管理 skill",
  "新建 skill", "盘点 skill", "沉淀 skill".
---

# skill-hub

本地网页管理系统，用于盘点、沉淀和治理本机全部 Claude Code skill / 命令 / Agent。

## 启动

```bash
sh ~/skill-hub/serve.sh
```

打开 http://127.0.0.1:3458 。常驻（开机自启 + 崩溃拉起）：

```bash
launchctl load ~/Library/LaunchAgents/com.petermini.skillhub.plist
```

## 能力

- **总览**：统计（本目录 / 链接挂载 / 未挂载 / 插件 / 命令 / Agent）+ 体检报告（断链、重名、描述逐字重复、.bak、缺 SKILL.md、Agent 缺工具）。
- **Skills**：五个来源统一视图 —— `~/.claude/skills/`（本目录 + 链接）、`~/.agents/skills/`（未挂载休眠）、插件缓存。支持搜索、状态过滤、查看详情。
- **编辑**：本目录技能可直接编辑保存（⌘S 快捷保存）；上游管理的技能（插件 / symlink）只读。
- **沉淀**：对上游技能一键「复制为自己的」→ 写入 `~/.claude/skills/<新名称>/SKILL.md`，成为你可随意改动的自有版本。
- **挂载 / 卸载**：休眠技能 symlink 挂载进 `~/.claude/skills/`，或卸载回休眠。
- **归档**：本目录技能移动到 `~/skill-hub/archive/`（带时间戳，可恢复），不硬删。
- **新建**：名称 + 描述 + 正文，自动生成 frontmatter 写入标准位置，Claude Code 即刻发现。

## 安全边界

- 服务绑定 127.0.0.1，仅本机可访问；写操作只落在 `~/.claude/skills/`、`~/.claude/agents/`、`~/.claude/commands/` 与 `~/skill-hub/archive/`。
- 名称校验正则 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`，路径不可逃逸。
- 插件技能为插件系统托管，hub 不直接修改；想改请「复制为自己的」。

## 备注

- 扫描实时进行（每次请求重扫），外部改动（手动编辑文件）刷新即见。
- 项目级技能（`<项目>/.claude/skills/`）不在扫描范围，属未来扩展。
