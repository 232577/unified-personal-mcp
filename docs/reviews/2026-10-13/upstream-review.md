# 2026-10-13 上游源码和分发核对

本轮从官方 GitHub fetch/clone 实际源码；以下日期、提交、差异是本轮核对结果。开始核对时本地 HEAD 为 `6c610c4bf3bc8171c06a72f0cecc94877bdccb73`，工作区 clean；后续同轮实现由各负责代理独立验证，不把上游自身测试当成本项目通过证据。

## 当前上游

| 项目 | 当前 main | 最近提交日期 | 发布版本/许可 |
| --- | --- | --- | --- |
| [Coding Tools MCP](https://github.com/xyTom/coding-tools-mcp/commit/d7c2dda48bcedbd066c7dbc24a1b63205384d269) | `d7c2dda48bcedbd066c7dbc24a1b63205384d269` | 2026-10-05T21:51:43-07:00 | 最新公开 release [v0.5.0](https://github.com/xyTom/coding-tools-mcp/releases/tag/v0.5.0)，tag commit `aa1cbf3832e0c85994600f2249d9544fbae2f49e`，commit 时间 2026-09-30T06:48:59Z；Apache-2.0 |
| [Remote Desktop Commander](https://github.com/desktop-commander/remote-desktop-commander/commit/b480501dcca59f802ebaf97f2f57b45252d0b720) | `b480501dcca59f802ebaf97f2f57b45252d0b720` | 2026-08-06T08:41:28+03:00 | server.json 为 1.0.22；文档/manifest 仓库；商业服务实现不公开 |
| [DesktopCommanderMCP 本地 server/device agent](https://github.com/wonderwhy-er/DesktopCommanderMCP/commit/d38179bed7d2a7f0d9beeaef2080a25614134b63) | `d38179bed7d2a7f0d9beeaef2080a25614134b63` | 2026-10-12T17:11:02+03:00 | package.json/server.json 为 0.2.52；[最新正式 release v0.2.52](https://github.com/wonderwhy-er/DesktopCommanderMCP/releases/tag/v0.2.52) 发布于 9月29日，release commit `c774c3b`；MIT。main 内另有 v0.3.1 alpha 分支合并，不能称为已发布正式 0.3.1 |

远程仓库 [README](https://github.com/desktop-commander/remote-desktop-commander/blob/b480501dcca59f802ebaf97f2f57b45252d0b720/README.md) 与 [LICENSE](https://github.com/desktop-commander/remote-desktop-commander/blob/b480501dcca59f802ebaf97f2f57b45252d0b720/LICENSE) 明确只允许浏览和链接；未经书面许可不得复制、修改和再分发资料。**本轮没有获得商业云端源代码，也没有集成其云端服务。** MIT 的本地源码授权是另一个仓库的授权，不覆盖商业服务。

## 对照当前集成

本项目 Coding Tools 共同祖先仍是 `5d6e131afebd89f98438b1c1dca8d157c0713c8a`（0.3.0）。相对最新 main，coding_tools_mcp/tests/pyproject.toml 共35个文件差异，约10,573行新增与328行删除；不是可以无风险整体同步的小更新。本项目已增加工作流所有权、Windows Job、full_control、静默本地遥测替代、统一目录和远程生命周期。

本项目 DesktopCommanderMCP provenance 固定在 `a781f5a4b8cfebac6638bc6fcbd38fca6326be53`（0.2.50），实际复用 PATHEXT 修正与有界渐进搜索思路。本轮上游已到0.2.52/main进一步修改，不应仅改 provenance 版本冒充整体升级。

### 值得本轮定向移植

- `Runtime.read_file` 按 LF/CRLF/CR 物理行处理续读，避免字节截断尾换行造成跳行或重复。来源：[2f1c7ff](https://github.com/xyTom/coding-tools-mcp/commit/2f1c7ff2b4e753f4a02a4f9440884ce84b9991f9)。续读携带原 end_line/max_lines 的范围，读完请求范围就不再建议越界续读：[2382305](https://github.com/xyTom/coding-tools-mcp/commit/238230544628d918c60f81a7c27b553fff52e841)。需要本项目覆盖混合换行、长行、范围耗尽与仅文字消费结果的测试。
- `protocol._call_tool` 只把 null/省略 arguments 转为 `{}`；false、0、空列表不再被 `or {}` 悄悄吞掉。来源：[dbc99d6](https://github.com/xyTom/coding-tools-mcp/commit/dbc99d624ebb7e39dccf37d971e3a162234f3421)。该修复不需要引入上游新遥测 hook。
- Git 与 fd 子进程字节边界：不要由 Windows text-pipe 线程吞掉 UnicodeDecodeError，也不要替换字节后把损坏路径交回文件工具。Git `_run_git_text` 先收 bytes、显式 UTF-8 与临时 `git -c i18n.logOutputEncoding=UTF-8`，无法解码则结构化错误；fd 使用 bytes 路径与 os.fsdecode。来源：[b13d0d4](https://github.com/xyTom/coding-tools-mcp/commit/b13d0d4c6c92327f055ad01df621458ca17ecef4)、[11b29f6](https://github.com/xyTom/coding-tools-mcp/commit/11b29f6)、[最终整合 c832222](https://github.com/xyTom/coding-tools-mcp/commit/c83222207e6dceb30b70299f0d76f5f66854b8b1)。上游 processes.py 的普通命令流仍有 UTF-8 errors=replace；不能称为“所有命令输出编码都已修好”。

### 后续有价值，但本轮不整体同步

- 0.5 `read_file` revision + `apply_changes`，精确基于已读版本修改，补丁幂等键和 after-edit 证据。需要同步 schemas、catalog、tool_policy、workflow写入门控、结果摘要和取消/重放行为；直接新增工具会撞本项目 explicit policy 白名单，不能只复制server模块。[0.5迁移说明](https://github.com/xyTom/coding-tools-mcp/blob/d7c2dda48bcedbd066c7dbc24a1b63205384d269/docs/migration-0.5.md)
- 分开进程生命周期/首次yield、首次观察完成后起算输出TTL、git_diff加入未跟踪文件。都有业务价值，但会改变当前客户端合约，应单独规划一次可回滚升级。上游全局失败breaker现改为advisory；本项目不应引入跨owner错误状态或外发遥测。
- 本地 MIT [remote-channel.ts](https://github.com/wonderwhy-er/DesktopCommanderMCP/blob/d38179bed7d2a7f0d9beeaef2080a25614134b63/src/remote-device/remote-channel.ts) 的重入保护、限时recreate、jitter/backoff、退避期自愈检查可作为设计参考。本项目 TunnelSupervisor 已有单次恢复线程、退避/jitter及“live child探测失败不替换”控制，直接引入 Supabase/商业认证频道会改变OpenAI-only远程边界，价值不足。
- MIT [诊断脱敏器](https://github.com/wonderwhy-er/DesktopCommanderMCP/blob/d38179bed7d2a7f0d9beeaef2080a25614134b63/src/remote-device/diagnostics/redact.ts) 覆盖 JWT/Bearer/token/email/host/home。只有新增自由文字诊断导出时才需要；当前CallDiagnostics使用字段允许列表，优先保留比事后正则更窄的收集边界。
- [显式工具annotations](https://github.com/wonderwhy-er/DesktopCommanderMCP/commit/8d6d2672e3ee44c5e6dc2c15f9cbccc9a94f5dd7) 与客户端中立metadata值得继续核对；本项目已有完整descriptor/policy定义，不应复制其终端任意PID kill、安全模型和品牌。

## 公开分发依赖边界

97包清单含 fuzzywuzzy 0.18.0（metadata GPLv2）、Levenshtein/python-Levenshtein 0.27.5（GPL-2.0-or-later）。本轮实读 windows_mcp/desktop/service.py:23 同进程 `from fuzzywuzzy import process`，并在窗口/app匹配两处使用 extractOne。不是单纯把独立软件文件并列存放。

[GNU官方FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#IfLibraryIsGPL)解释组合程序与库使用的许可；[兼容说明](https://www.gnu.org/licenses/license-compatibility.html)说明Apache-2.0与GPLv2不兼容、与GPLv3兼容。fuzzywuzzy LICENSE附录的“如何应用GPL”通用示例含or-later，不能把该示例当作fuzzywuzzy项目本身确实授予GPLv3选择权的证明。因此**只给3包源码归档和许可证，不能自动证明完整Apache产品分发合规**。

本轮执行选择：本机个人完整包保留在本地；公开发行源代码和源码升级包，不包含Python依赖、浏览器或其他第三方二进制。官方源个人安装把依赖获取留在用户设备上；这不等于对其他可能的分发方式作出许可证结论。

如果以后选择公开完整二进制，应先解决组合许可（核实许可证/获得许可或替换GPL依赖，例如经评分与窗口选择回归的MIT RapidFuzz），再随二进制提供对应源：准确版本来源、上游修改、依赖源码/生成接口、编译安装脚本与可复现参数、hash清单、原始license/NOTICE。不能用漂移的上游首页替代对应源保留。Python、Chromium、ripgrep和tunnel还有自身分发要求，应按实际binary清单审查，不限于这97项metadata。

## 源码升级和换机

真实0.1.10 ZIP：requirements-windows.lock与当前字节相同；bin全部4文件、tunnel-client全部5文件的SHA256相同；browsers 609个文件相同，唯一变化为headless debug.log（运行日志）。ZIP另含8507个Python文件；项目resources没有python目录，本来由完整包构建从独立Python源复制，不能据此推断不兼容。

源码升级工具先验证完整旧包和升级包的全文件集及hash，再验证lock与静态资源摘要，复制到全新sibling目录、整替换app、更新脚本/资料并重建manifest；不改变旧包、私有配置、计划任务或运行服务。update.cmd查找属于该产品管理描述的计划任务，找不到唯一基础包时由用户输入完整包目录。准备完成后仍需现有validate_package/isolated_startup_check及显式switch流程。

已运行老包如多出__pycache__、浏览器.links或debug.log发生变化，会被严格完整性检查拒绝。应使用本地保留的原始完整ZIP重新解压为干净base，而不是忽略未知文件或删除正在使用的老目录。新设备不能仅靠源码升级包首次启动；需要先按源代码安装步骤从各官方源获取精确依赖和运行时，再设置自己的工作目录/设备/隧道凭据，不携带旧机私有配置。

本轮新增 scripts/build_update.py、scripts/prepare_update.py、packaging/update.cmd 与21项升级回归。已先记录缺失实现/缺失模块/切换归属检查的失败，再实现并验证21 passed；Ruff clean；update.cmd 的嵌入PowerShell通过仅解析语法检查。

实际测试用原0.1.10包自带的Python，成功生成0.1.13独立候选目录，状态PREPARED/service_changed=false。最新源码升级ZIP重建为361,156字节，SHA256 `ab48caf595a3f69c604daa0a8751ed3e2917c78600d16ff2f619183349e08f2a`，112项payload+manifest，ZIP扫描无.exe/.dll/.pyd/.pyc或resources文件。该hash是本轮临时验证快照，最终发行须在所有源码变更完成后再重建并使用最终hash。

update.cmd 只准备新目录；`--apply`随后询问y/N。只有用户明确选择y，才用候选Python重新校验完整候选、核对本人ManagedTask确实指向base、读取原配置，并调用既有switch --run，58工具是本轮明确验收条件。切换证据写入原私有data_root/update-*；不打印密钥，不自动启动离线旧服务，不绕过无活动workflow、隔离启动与回滚检查。本子任务未执行真实apply，真实受控服务切换由主代理统一验证。

本报告不宣称0.1.11/0.1.12基础包已逐文件通过真实准备；这两版在支持集合中，但仍由相同lock/resources/full-manifest检查决定是否可用。运行过且产生新文件的旧目录应使用干净原始ZIP重新解压，源码准备脚本拒绝覆盖已存在候选目录；失败或选择暂不切换时保留候选以供核对。
