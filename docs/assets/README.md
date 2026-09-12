# 产品图与可编辑图源

| 图 | 用途 | 发布图 | 排版图源 | 结构图源 |
|---|---|---|---|---|
| 产品总览 | 主代理分工、任务卡、当前执行器和扩展边界 | [PNG](task-router-overview.png) | [SVG](task-router-overview.svg) | [Mermaid](task-router-overview.mmd) |
| 执行与验收 | 从提交、执行、取回到调用者验收 | [PNG](task-router-lifecycle.png) | [SVG](task-router-lifecycle.svg) | [Mermaid](task-router-lifecycle.mmd) |

两张图根据 2026-09-12 的已验证实现绘制。总览图的虚线表示尚未接通的扩展方向；第二张图展示正常返回与验收路径，底部说明失败和重启行为。图中的主代理分级与语义验收由调用者完成，不是服务自动识别。O / KR 对应任务约定，未增加同名 API 字段。

SVG 是手工排版的独立矢量图，PNG 是对应导出；Mermaid 是便于修改关系和流程的结构版本，排版不要求与 SVG 相同。SVG 包含 title / desc，无脚本和外部资源。PNG 保留既定字体，供 GitHub 和其他 Markdown 阅读器稳定显示。

如已安装 librsvg，可在仓库根目录重新导出：

```bash
rsvg-convert -o docs/assets/task-router-overview.png docs/assets/task-router-overview.svg
rsvg-convert -o docs/assets/task-router-lifecycle.png docs/assets/task-router-lifecycle.svg
```

字体使用系统字体回退；维护环境为 Avenir Next 与 Hiragino Sans GB。改动状态或流程时同步维护 SVG 与 Mermaid，并渲染检查 PNG。仓库 main 的首页使用同一张总览 PNG，更新时同步其副本。
