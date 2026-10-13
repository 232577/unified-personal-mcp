# 独立工作流查询复核（2026-10-09）

前置提交 `01fbbf0fe9136ce704551320f547ebd0a8e80466` 已交付本地诊断。本轮继续在同一开发分支增加固定只读查询，不改生产连接。

## 实现与兼容性

- 增加 `workflow_list` 与 `workflow_status`；原 50 工具保留。`UnifiedTask` 的写入提示不降级，服务端验证不放松。
- list 不要求已有工作流，调用认证所有者的原 registry.list；status 与旧入口共享 `_workflow_status`，继续用当前凭据及所有者校验，并在读取资源期间保留在途保护，`touch=False`。
- 查询结果 schema 禁止额外顶层字段，不包含秘密 workflow_id 的输出声明。状态从 RESERVED 到 ACTIVE/ENDED 均只观察，不隐式激活或回收。
- 自动测试和 Inspector 验收入口将真实目录计数从 50 更新到 52，并明确检查两个新工具存在；独立的旧 GUI 模拟数据和历史验证数字不批量改写。

## 自查结论

| 类型 | 依据与范围 |
|---|---|
| 架构 | 复用工作流 registry 与原权限/代次检查；没有第二套状态存储，没有绕过原结束保护。 |
| 代码 | 实际数据库工作流和凭据表在重复查询前后相等；禁止 touch/expire/end 的测试确保只读路径没有租期或清理副作用。 |
| 产品真实性 | 新工具只是两个查询能力，旧生命周期混合入口继续存在；不宣称全部 API 拆分、并发优化或平台拦截已解决。 |
| 安全 | 空参数 list 仍受 HTTP 认证和 owner scope 约束；禁止注入 action/principal。跨所有者及轮换后旧凭据被拒绝；输出不带工作流秘密或命令正文。 |

复核由实施者自查，不宣称独立审查或安全认证。实际单进程长写入锁回归确认查询可在写入等待期间进入；不把该测试当作负载测试或平台限流测试。

## 自动证据

- `verification/diag-20261009-queries-red.log`：16 个新测试全部因缺少独立查询工具而失败，退出 1。
- `verification/diag-20261009-queries-green.log`：实现后同一 16 项通过，9.65 秒，退出 0；包含真实认证 HTTP tools/list/call、原数据库与命令句柄验证。
- 首次补丁因 `close` 存在两个匹配位置被原子拒绝；Git 差异确认没有部分落盘后，使用唯一上下文重新应用。未进行整体回滚。
- `verification/diag-20261009-queries-full.log` / XML：628 项通过，102.81 秒，命令 `MwoWp8l5zCz26FitTn_baNXV` 退出 0。
- `verification/diag-20261009-inspector/result.json`：Inspector 2.7.0 / Node v22.23.2、14 项本地 legacy HTTP 检查 PASS、52 工具；命令 `vkIBn_ssoWw62R_RUhY-3pHl` 退出 0。预期认证与旧凭据失败被单独断言，不把所有子命令说成退出 0。隔离服务经 finally 正常清理，生产隧道未使用。

真实截图测试仍不执行；依赖版本、生产服务、自启动配置、真实隧道和业务工程不变。
