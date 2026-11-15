# 站点目录说明

`site/` 是一个**零构建、可直接由 Qoder Sites 发布**的静态项目主页：把本目录整体作为站点根目录上传即可，
入口是 `index.html`，无打包、无依赖、无后端。

## 文件

| 文件 | 作用 |
| --- | --- |
| `index.html` | 痛点、功能、点选演示、运行说明、评审入口、仓库链接 |
| `styles.css` | 站点样式（响应式，手机可读写） |
| `demo-data.js` | 浏览器内演示用的静态数据，内容与 `../samples/` 同一批虚构记录、行号一致 |
| `demo.js` | 演示交互（检索、筛选变化、对比与摘要）；明确标注不是 seekdb 实例 |
| `screenshots/` | 本地应用的检索证据和实验对比截图 |

## 样例与实测

浏览器演示用虚构样例做子串匹配。本地版使用 seekdb 的 `FULLTEXT` 索引做词元检索，可运行 `pnpm run explain 复现实验` 查看执行计划。

- [检索学习率](./screenshots/search-evidence.png)展示命中文段、记录名、行号和实际 SQL 执行计划。
- [跨数据集对比](./screenshots/compare-warning.png)展示数据集、划分和指标单位不一致的提示。
