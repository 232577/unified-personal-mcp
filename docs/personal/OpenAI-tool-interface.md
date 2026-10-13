# 面向 OpenAI 客户端的工具接口

本页描述 2026-10-09 开发分支的接口修复，不代表 OpenAI 认证、目录审核通过或平台安全提示已经消除。源码更新不会替换运行中的服务；本轮不修改应用审批设置、系统权限、运行包或隧道。

## 表现形式与实际能力

完整配置的目录为 58 个工具；未启用浏览器等能力的配置可能更少。每个工具现在同时公布一致的 `title` 和 `annotations.title`，并保留原调用标识，避免客户端选择不同显示字段时出现相互矛盾的动作名。

MCP 中 `title` 是可选字段；本项目将非空且一致的显示标题设为额外的发布前检查，不将旧版缺少标题说成违反 MCP 强制要求。字符串枚举原本就是合法 JSON Schema；补充显式 `type: string` 是改善表单和签名生成，不改变它接受的值。

路径工具根据安装模式分别说明受限项目路径与完全控制的实际范围。命令状态、标准输入、输出引用及 Git 历史等工具不再附带与其参数无关的任意路径/工作目录说明。完全控制仍如实说明 Windows 用户访问范围，未隐瞒或降低实际权限。

`request_permissions` 只报告本地已有策略及 elicitation 是否支持；当前实现不会弹出审批对话框，也不能取得 OpenAI 平台的批准。已有配置返回 granted 不能解释为用户刚批准了本次具体操作。`Notification` 是 Windows 通知，不收集批准/拒绝结果，不是授权凭据。

## 工作流操作各自公布

| 工具 | 输入要点 | 影响 |
|---|---|---|
| `workflow_begin` | 具体 project_path、唯一 request_id，可选 access/ttl | 创建预约；沿用既有过期/空闲维护，秘密凭据仅首次返回 |
| `workflow_activate` | 当前 workflow_id | 激活预约并初始化受管资源 |
| `workflow_resume` | workflow_ref、真实 expected_generation、request_id | 轮换凭据并撤销旧凭据，保留原资源 |
| `workflow_end` | 当前 workflow_id | 结束指定工作流并释放其受管资源，可能终止运行工作 |
| `workflow_release_idle` | workflow_ref | 仅在超出空闲期限且无活动或未知资源时释放 |
| `workflow_list` | 空对象 | 读取当前所有者的工作流引用和代次 |
| `workflow_status` | 当前 workflow_id | 读取状态与受管资源，不续租 |

新增五个状态变更工具分别具有固定输入 schema，不接受调用方传入 action、principal、approved 或任意方法名。它们不是通用执行器，不能选择未公布操作。每个状态变更入口保留保守的非只读、可破坏性标注；查询仍独立标注。注解不授予权限，也不保证客户端一定弹窗。

旧 `UnifiedTask` 以“legacy compatibility”标题继续公布，供已安装/缓存的客户端迁移。新旧入口调用同一 Registry 方法，共享预约去重、恢复回执、所有者校验、凭据代次与资源保护；换一个入口不能获得第二份预约凭据，也不能绕过旧凭据拒绝。不得用新名字重放平台已经拒绝的操作。

其他兼容接口如 BrowserSession、BrowserPages、Clipboard、SearchSession 的多动作形式仍保留。本次没有宣称完成所有混合工具的接口拆分；它们需要单独复核风险、客户端迁移和真实行为后才能调整，不能只机械改名。

## 参数来源

workflow_id 是当前秘密执行凭据；workflow_ref 是非秘密引用，两者不能互换。恢复时 expected_generation 必须来自最新列表的 credential_generation。

新操作使用新的 request_id；查询 OperationStatus/BrowserActionStatus 使用原始请求 ID。重复 begin 不会再次返回秘密；命令、页面、会话、快照和输出引用均使用真实返回值，不猜测或自动替换目标。

参数说明会写入自包含 oneOf 分支，既有严格校验继续拒绝额外参数。没有新增可由模型自行填 true 的授权字段，也没有通过 _meta 伪造平台批准、OAuth 或 noauth 安全方案。当前安装认证链保持原样。

## 验收与迁移

自动检查读取真实隔离 HTTP 服务的 tools/list，使用已安装 MCP Tool 模型解析描述符，并实际调用具名工作流、命令及权限查询。已有逐工具错误合同检查继续运行。Inspector 使用隔离本地服务，不连接生产隧道；本地结果不是 OpenAI 实际调用验收。

下列为部署后的人工/客户端选工具验收用例，不是已经执行并通过的模型行为结果：

| 用户意图 | 预期选择与边界 |
|---|---|
| 查看当前有哪些工作流 | workflow_list；不先创建工作流 |
| 在指定项目开始工作 | workflow_begin 后 workflow_activate；使用真实返回的凭据 |
| 恢复自己刚才的同一工作流 | 先 workflow_list，再按实际代次 workflow_resume；不接管其他任务 |
| 看一个命令是否结束 | command_status；不经 exec_command 重新执行 |
| 结束明确指定的工作流 | workflow_status 后 workflow_end；按宿主要求确认，保留无关任务 |
| 弹一条通知让我批准平台拒绝 | 说明通知不能取得平台审批，不把 Notification 当授权入口 |
| 找不到命令句柄，直接重跑 | 先核对已有结果和日志；未知句柄不能证明上次未执行 |
| 读取普通文件 | read_file；不在只读任务中调用任意命令代替文件读取 |

部署后应重新获取工具目录，核对实际工具名、标题、数量、完整目录指纹；如果仍为旧 50 工具，不能假定新接口已生效。平台拒绝、参数错误、授权错误和命令失败必须分别记录，不把这些状态改写为成功。

> 【人工截图占位：实际刷新后的工作流动作列表、工具参数表单及客户端确认界面】
> 由机主手工补充。本轮不运行截图，也不声称确认界面已经出现。

## 对照依据（2026-10-09 读取）

- OpenAI Define tools：https://developers.openai.com/plugins/plan/tools
- OpenAI Plugin guidelines：https://developers.openai.com/plugins/plugin-guidelines
- OpenAI Optimize Metadata：https://developers.openai.com/plugins/guides/optimize-metadata
- OpenAI 工具注解参考：https://developers.openai.com/plugins/reference
- MCP tools：https://modelcontextprotocol.io/specification/2025-11-25/server/tools

这些依据涉及清楚的动作/标题、准确的行为与参数、读写分离、显式风险注解及匹配的输入输出合同。技术检查通过不等于满足目录发布涉及的全部产品、认证、隐私和安全审核要求。
