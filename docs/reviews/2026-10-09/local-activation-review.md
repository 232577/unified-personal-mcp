# 0.1.12 本机续接与运行验收

本次按机主提供的断点续接要求维护 `D:/run/unified-personal-mcp`。开工分支 `feat/mcp-diagnostics-20261009`，HEAD `3dba139136ee6f9182b079f3d5f7bd9d37131697`，工作树干净。工程与部署使用本地工具，不依赖待切换插件。未运行截图、浏览器截图或真实截图验收脚本；未修改软件工厂业务工程，也不推送远端。

## 初始状态与范围

本机认证 HTTP、计划任务、实际进程路径和只读数据库观察一致：运行 0.1.11，50 工具，权限为 full_control；自启动为 `UnifiedPersonalMCP-AutoConnect`。查询时无活动工作流，数据库仅有 232 个 ENDED，活动资源均为 0；只观察到服务拥有的隧道及控制台辅助进程。这个快照不能代替停止前的再次检查。

r2 的清单、文件集合、逐文件摘要、ZIP CRC 和 sidecar 通过，81 个模块逐字匹配开工工作树。与 HEAD 的 15 个模块只有 LF/CRLF 差异，规范化后源码与 AST 一致。ZIP 为 486199600 字节，SHA256 `3600c61d64f82b75bba2b76ce6cbb57d200be79f93354db0a1cd6ff832d13dfa`。0.1.11 回退包也通过校验，未覆盖。包内 verification.md 属于构建快照。

## 本次发现与最小修正

只读复核及模拟回归发现：安装器登记后的最后状态读取不在恢复保护内；切换脚本再读登记失败时丢失回退依据；等待进程退出期间的外部登记修改可能被回滚覆盖。

修正将最后状态读取纳入保护，返回已验证的任务元数据供切换保存 XML 收据，恢复前在同一 COM 操作中比对当前 XML。直接本机 `--run` 可省略工作流引用并严格要求空闲；不伪造工作流，也不创建或删除一次性任务。这些修改不更改权限、风险注解或隧道认证。

由于修改了随包运行模块 autostart.py，最终部署必须使用新建的独立 r3 候选，r2 保留原样。不会用更新清单掩盖旧包源码差异。

## 验证与部署结果

所有原始证据保存在 `verification/recheck012-20261009/`，历史输出未覆盖：

- 完整非截图回归：746 passed，219.98 秒，退出 0；XML errors=0、failures=0、skipped=0。命令 `.venv-host/Scripts/python.exe -B -X utf8 -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=.tmp/q12f1 --junitxml=verification/recheck012-20261009/full.xml`。父助手有限超时 900 秒，日志 `full.log`，真实退出记录 `full-process.json`。
- 切换专项：30 passed，0.28 秒，退出 0，包含真实临时计划任务的启停、XML 冲突拒绝及清理。命令同上替换测试目标为 `tests_personal/test_release_switch.py`，basetemp 为 `.tmp/q12s1`；证据 `switch-tests.log`、`switch-tests.xml`、`switch-tests-process.json`。专项父助手有限超时 300 秒。
- 新候选 `release-private/UnifiedPersonalMCP-0.1.12-r3` 构建退出 0，153.049 秒；固定 Python 3.13.14 x64 与现有 resources，未覆盖旧包。构建命令为 `.venv-host/Scripts/python.exe -B -X utf8 scripts/build_portable.py --output release-private/UnifiedPersonalMCP-0.1.12-r3 --python-home D:/run/bf-desktop-control/tools/python-runtime/cpython-3.13.14-windows-x86_64-none --assets resources --zip`；日志 `build-r3.log`。
- 新 ZIP 为 486201037 字节，SHA256 `060d2860b63d61179d66829e54d34eb9cbed7024cd3a3e48f08f38af73b60408`。9233 清单文件、文件集合、逐文件 SHA256、ZIP CRC/集合/摘要/sidecar、私有文件排除均通过。81 个运行模块及独立入口逐字匹配冻结工作树；证据 `r3-integrity.json`，验证退出 0。
- 用 r3 自带解释器 `-B -s` 及包内模块完成 23 次认证 HTTP 工具调用和 tools/list。具名工作流、原始回执、非法输入后查询、真实退出 7、58 工具、完整描述符指纹与诊断字段检查通过；finally 自有工作流结束、隔离服务停止。`r3-http-acceptance/result.json` 和 `r3-http-process.json`，退出 0。这不是生产隧道或 ChatGPT 调用。
- 四个修改代码/测试文件的 Ruff 和 `git diff --check` 通过。未运行真实截图，合成图像契约测试仍保留；不执行 acceptance_local 或截图验收脚本。

## 已完成的实际切换与本地调用

运行源码修正已提交为 `cb0b50da0b5a677f54c9d920de8ff99f4d46e3b9`；81 个包内运行模块与该提交的规范化字节及 AST 全部一致，工作树当时干净。源码提交不推送远端。r3 的说明文件是构建时快照，不改写包清单补入后续证据。

切换前再次通过认证 HTTP、只读 SQLite、当前任务及进程身份确认零活动工作流和资源。执行命令为 `.venv-host/Scripts/python.exe -B -X utf8 scripts/switch_managed_release.py --run --config C:/Users/Administrator/AppData/Local/UnifiedPersonalMCP/settings.local.json --candidate D:/run/unified-personal-mcp/release-private/UnifiedPersonalMCP-0.1.12-r3 --old-bundle D:/run/unified-personal-mcp/release-private/UnifiedPersonalMCP-0.1.11 --version 0.1.12 --old-version 0.1.11 --tools 58 --output D:/run/unified-personal-mcp/verification/recheck012-20261009/switch-live`。父助手限时 900 秒；真实退出 0，34.195 秒，结果 `LOCAL_READY`，没有执行回滚。日志/阶段记录/结果为 `switch-live.log`、`switch-live/events.jsonl`、`switch-live/result.json`、`switch-live-process.json`。

实际服务与下次登录目标同为 `release-private/UnifiedPersonalMCP-0.1.12-r3`，运行的是该包的 `resources/python/pythonw.exe`。当前任务 `UnifiedPersonalMCP-AutoConnect` 归属正确、启用且运行；旧登记备份与切换前 XML 摘要一致。旧 Unified 及其拥有的隧道/辅助进程已按创建时间核对退出，没有竞争实例。没有停止 BF 或无关服务，没有重启 Windows。

真实运行实例认证 HTTP 验收退出 0：58 工具和具名接口、工作流创建/激活、文件补丁/读取、命令执行与观察、原请求 OperationStatus、非法参数拒绝后的继续查询、跨目录文件和命令目录全部通过。三个实际根命令退出码分别为 0、7、0；整个原始工具结果与各自回执的规范化字节一致。结果 `live-http-acceptance/result.json`，进程记录 `live-http-process.json`；仅使用本轮新建隔离目录与自有工作流。

最终诊断 `catalog.json` 与认证 tools/list 一致，完整描述符指纹 `0fc6d288141596b54af89b33e74557054466b0298a8a1860a8295009224dfbe4`，格式 `complete-descriptors-v2`，未观察到诊断事件丢失。配置文件、backend/control/tunnel 三类密钥摘要前后一致；权限继续为原 full_control。隧道自身 `/healthz` 与 `/readyz` 均 HTTP 200，当前监督状态 healthy。MCP 监听端口本身不提供这两个 GET 路由，未将其 404 误判为隧道离线。

最终自有工作流 ENDED；数据库仅有 233 个 ENDED，列表为空，所有活动资源和保留命令为 0。只剩一个生产服务实例及其隧道/辅助进程，Unified 命名任务仅正式自启动一项，无临时切换或测试任务。`final-live.json`、`final-receipt.json` 保存交叉核对；真实调用后再次校验 r3 全包通过。临时文件和历史证据保留，不删除旧包或用户资料。

Task Scheduler 没有事务式 compare-and-swap；恢复在同一 COM 操作内临写前重新核对 XML，但不能承诺独立外部编辑器的所有微小竞争都被排除。本轮实际没有出现登记冲突。

本段初始的待验收项现已续接：ChatGPT 应用目录刷新后显示 58 项，实际 Unified 插件的兼容工作流、文件、命令、原结果查询和跨目录链通过，详见 [客户端隧道复核](client-tunnel-review.md)。旧对话仍绑定原 50 项，新增具名接口没有在该旧绑定中逐项云端调用。此前平台明确拒绝的请求不重放，不调整平台保护或伪造批准；一次验收不证明所有平台安全提示永久消除。
