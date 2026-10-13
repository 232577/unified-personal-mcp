# 0.1.13 修复与发布验证

本轮以本机实际运行的 0.1.12 为部署基线，修复源码、核对公开上游、制作源码升级包并验证受控切换。用户明确授权 GitHub 发布与两台设备更新；另一台设备的 local MCP 隧道不可达，用户确认暂时无法操作该电脑，因此远端更新仍待执行。Remote Desktop Commander 仅作公开源码研究，后续未访问其设备。

## 范围与修复

- 文件读取按物理行分页，续页保留原 end_line/max_lines 范围；不再因为末尾换行漏行。
- 拒绝 tools/call 的非对象参数（[]、false 等），保留 null 与省略参数兼容。
- Git 查询明确按 UTF-8 解码，无法无损解码时返回结构化错误；Git ignore 的路径传递使用字节，避免 Windows 代码页损坏中文路径。
- 只读 Git 查询禁用 external diff、textconv、fsmonitor 和 clean/process/smudge filters；不展开子模块工作树辅助程序，保留 gitlink 提交变化并明确返回限制说明。
- 命令缓冲淘汰后，轮询用累计字节量比较绝对游标，避免已有新输出仍等到超时。
- full_control 权限查询如实说明现有本地配置；敏感环境变量仍过滤，不声称已经取得平台或用户审批。
- 达到操作记录上限时，只回收 registry 确认永久 ENDED/EXPIRED 的其他工作流回执；活动、未知和清理受阻任务保留去重保护。
- 升级失败回执记录白名单错误码，不暴露任意异常文本；维护脚本同时支持源码和便携 app 目录。

运行审查、上游核对与安全复核分别由独立子代理执行，主代理整合并重跑验证。安全审查范围及未覆盖项见 [静态审查报告](../../../reports/security-2026-10-13/static-review.md)，并非完整 Codex Security 标准扫描。上游来源与采用/暂缓理由见 [源码比较](upstream-review.md)。

## 本轮验证

- 配置验收：`python -B -m pytest -q tests_personal tests_bf tests/test_runtime_boundary_regressions.py --basetemp=verification/release-013-final --junitxml=verification/release-013-final.xml`，**795 passed、1 skipped、10 subtests passed，247.27 秒，退出 0**。唯一跳过是 POSIX 非 UTF-8 文件名测试，Windows 不适用。
- Ruff 对产品、测试、修改的 coding 模块和维护/升级脚本检查通过；`git diff --check` 通过。
- Git helper、子模块 helper、分页/协议/编码与输出游标均有修复前失败复现；源码升级专项 21 项通过。根代理维护/升级相关 97 项通过。容量闭环先失败后通过，相关 22 项通过。
- 原始 0.1.10 完整包自带 Python 成功执行实际源码升级准备，生成新的 0.1.13 目录；没有接触另一台电脑或宣称该电脑已经更新。
- 本机 0.1.12-r3 完整包生成 0.1.13 候选，**9237 个清单文件**校验通过；81 个模块文件与当前源码、公开升级包逐字节一致。
- 实际包内 Python 执行认证 HTTP、命令去重、凭据轮换/旧凭据拒绝、中文搜索、浏览器导航/观察/截图和资源结束，结果 PASS；58 工具，隔离测试未启动生产隧道。见本地 `verification/release-013-portable-smoke/result.json`。
- 公开升级 ZIP **364601 字节**，112 个载荷文件；ZIP CRC、文件集合与每文件 SHA256 通过。SHA256：`54ea043d8f526fae6573f62fa36f82dd6ead07f0d9a94e2042ec6a6bc62fbef6`。无 Python/浏览器二进制、运行配置、密钥、状态数据库或业务数据。
- 发布前对待提交文本扫描常见 OpenAI/GitHub token 和私钥标记，没有候选命中。该检查不等于完整秘密检测保证。

## 验证限制

直接运行上游通用 coding 套件得到 155 passed、18 failed、122 skipped、290 subtests passed。失败包含现有 Windows/POSIX 命令和路径差异、上游项目文档/CI 预期与本产品不同、父 Git 仓库影响的夹具和 Windows HOME 环境；没有将该套件声称全绿。原始输出保留在本地 `.tmp/runtime-audit/coding-suite.txt`。

超长单行仍只能在字节上限内预览，并明确 `first_line_exceeds_limit`。只读 Git 不检查子模块工作树脏状态，返回 warning；需要深入子模块时单独选定其项目。未在 Linux 运行沙箱验收，未做本轮全部第三方依赖漏洞扫描。

公开发行只含源码及源码升级包；包含 GPL 依赖的个人组装运行环境保留在本机，没有将其当作已解决完整再分发许可的公开二进制。切换是短暂断线的受控服务重启，不是零中断内存热重载。

## 本机实际切换与客户端验收

归属当前用户的管理任务从 0.1.12-r3 切换到新的 0.1.13 完整包，返回 LOCAL_READY。旧包保留，没有重启系统或控制其他服务。新服务 PID 34732，版本 0.1.13，58 工具，本地健康和隧道健康均正常；回执在 `verification/release-013-switch/result.json`。

随后通过真实 Unified Personal MCP 插件完成 server_info → workflow_begin → workflow_activate → apply_patch → read_file 两页 → exec_command → command_status → workflow_end。文件前两页分别准确返回 line1 与 line2；命令输出 release-0.1.13-ok、退出码 0；观察确认输出完整；工作流最后为 ENDED。生产 full_control 的工具指纹 `0fc6d288141596b54af89b33e74557054466b0298a8a1860a8295009224dfbe4` 与切换前一致。证据保存在无工作流秘密的 `verification/release-013-plugin-acceptance.json`。这是这些实际调用通过的证据，不代表所有未来操作都不会触发平台安全检查。
