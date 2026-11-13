# 课页

课页把 UTF-8 笔记整理成可检索的知识库。你可以用术语或自然语言问题搜索，结果会显示命中的原文和段号。浏览器用 BGE Small 对笔记和查询做向量化，seekdb 存储原文与向量并完成检索。

## 启动

需要 Node.js 22.12+、pnpm 10.33.0 和最新版 Chrome 或 Edge。随包的 seekdb 1.4.0 运行库支持 Windows x64、Linux glibc x64/arm64 和 macOS Apple Silicon。首次启动会初始化数据库，需预留 2 GB 以上的可用内存和足够磁盘空间。

```bash
npm install --global pnpm@10.33.0
pnpm install --frozen-lockfile
pnpm models
pnpm build
pnpm start
```

打开 [本机课页](http://127.0.0.1:4180/)。`pnpm models` 下载固定版本的模型文件并核对 SHA-256，之后由本机应用供应模型和 WASM 文件。应用通过官方 `@oceanbase/seekdb-node` 启动本机数据库，通过 `seekdb` JavaScript SDK 连接和查询。在 Linux 和 macOS 上，应用通过随机回环端口转接本机数据库的 Unix socket。默认数据库目录是 `.runtime/library`，停止应用后保留数据。两次独立复现可使用不同的 `NOTES_DATA_DIR`。

已有 seekdb 1.4.0 时，可设置 `NOTES_DB_HOST`、`NOTES_DB_PORT`、`NOTES_DB_DATABASE`、`NOTES_DB_USER`、`NOTES_DB_PASSWORD` 后运行 `pnpm start`。附带的 `compose.yaml` 可启动一个仅绑定本机的 seekdb。设置 `NOTES_DB_PASSWORD` 后执行 `docker compose up -d`，等待数据库就绪，并以 `NOTES_DB_HOST=127.0.0.1`、`NOTES_DB_PORT=12881` 运行应用。Windows PowerShell 用 `$env:变量名='值'` 设置环境变量。建议使用独立数据库验收。

## 手工验收

| 操作 | 预期结果 |
| --- | --- |
| 点击 **导入示例笔记**，输入「二级索引为什么要回表？」 | 导入 28 段，混合检索结果出现《主键以外的第二次查找》和原文。 |
| 新建一个知识库，再导入包含「蓝松校验词」的 `.md` 文件 | 新库计数为 1，写入和索引刷新完成后可以搜到新增原文。 |
| 再导入同一文件 | 提示跳过重复段，计数保持不变。 |
| 在知识库间切换，再刷新页面 | 切换时清除旧结果，刷新后恢复所选知识库及其计数。 |
| 在有 WebGPU 的环境打开应用，在无 GPU 的浏览器会话重复导入和搜索 | 两种环境都完成流程。状态分别显示 `WebGPU · 浏览器推理` 和 `WASM · 浏览器推理`，后者提示已自动降级。 |

单文件上限为 180 KiB，每批最多 320 KiB 或 100 段。请分批导入更长的文档。此应用面向单机个人笔记。

## 复现实测

重启应用前设置 `NOTES_ACCEPTANCE=1`，允许脚本建立 FP32 基线集合。安装 Playwright Chromium 后执行 `pnpm acceptance`，也可设置 `NOTES_BROWSER=msedge` 或 `NOTES_BROWSER=chrome` 使用本机浏览器。`NOTES_TEST_PROFILE=wasm` 只跑 WASM，`gpu` 要求有可用 WebGPU，默认两者都验收。在无 GPU 的环境默认报告实际运行的 WASM 结果。

```bash
pnpm exec playwright install chromium
pnpm test
pnpm acceptance
```

脚本在新浏览器会话中阻断非本机网络请求，先用真实界面验收，再用 28 篇原创示例笔记和 28 条标注查询测量模型、检索和批处理。输出在 `.runtime/evidence`，可用 `NOTES_EVIDENCE_DIR` 改路径。模型与实验说明见 [技术说明](TECHNICAL.md)。

模型来自 [BAAI BGE Small 中文模型](https://huggingface.co/BAAI/bge-small-zh-v1.5)，采用 [Xenova 的公开 ONNX 版本](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/7a21cfa835a0d8cba006e36210e7843b232a1107)，其模型许可证见上游。推理使用 [Transformers.js 3.8.1](https://huggingface.co/docs/transformers.js/v3.8.1/en/guides/dtypes)，数据库使用 [seekdb-js](https://github.com/oceanbase/seekdb-js)。示例语料与查询标注位于 `data/sample.mjs`。
