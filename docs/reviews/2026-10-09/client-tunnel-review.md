# 0.1.12 r3 客户端目录刷新与实际隧道验收

本轮续接本机启用后的客户端验收，没有修改运行源码、权限或服务登记，也未重启服务、Windows 或其他项目。使用已连接的 Unified Personal MCP 插件实际调用；客户端链中的文件与命令操作没有通过本地 HTTP 代替。只使用本项目新建的隔离目录，不使用历史秘密凭据或先前被拒绝的请求。

## 工具目录刷新

通过 Edge 的页面结构打开已安装应用 `asdk_app_6ab0bf771bd4819180889032d0e9e4e1` 的设置，点击一次“刷新工具”。刷新后应用工具弹窗实际显示 **Write 28、Read 30，共 58**；workflow_begin、workflow_activate、workflow_resume、workflow_end、workflow_release_idle、workflow_list、workflow_status、command_status 均已列出。原权限显示 Allow low-risk，未修改。

只读取页面结构，没有截图，也没有调用桌面截图工具。应用展示版本 1.0.0/dev mode 与服务的运行版本属于不同字段；实际插件 server_info 返回 0.1.12，不能用应用展示版本判断运行包版本。

官方刷新流程依据：[Connect and test your plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)，包括部署服务、刷新连接元数据、在新对话复测。当前长期对话的可调用绑定仍为原 50 项；这不等于刷新失败。新导入的 58 项目录已由客户端页面核对，新增具名工具在该旧绑定对话中没有逐项云端调用。

## 已完成的实际插件调用

首个实际插件 server_info 返回 0.1.12、58 工具、full_control、健康隧道；目录指纹 `0fc6d288141596b54af89b33e74557054466b0298a8a1860a8295009224dfbe4`，格式 complete-descriptors-v2，实例 `970d9425754748b8a178a55735783628`，与此前本机启用实例一致。本机诊断对应 UTC 06:46:39、序号 168–170、trace `ad894434eb254f479faa141a22c56eb7` 的服务端收到、校验、返回成功；真实客户端已收到的结果由插件返回证明，诊断自身的 client_receipt_verified:false 仍保留其原含义。

本轮工作流非秘密引用 `wfr_09c8481087457d64fde665dc5ee03f7f4b8bb20e0bf0651f514a668645030913`，临时目录 `.tmp/client012-1a11f6cd052-6927dc3d/`，秘密凭据只在调用期间保存在内存：

- 通过兼容 UnifiedTask 创建、查询预约并激活自身工作流，没有重复 begin 获取凭据。
- apply_patch 新建文件，read_file 回读并核对独立标记；exec_command 实际 echo 命令退出 0。
- OperationStatus 查询原 request_id，结构化原结果完整匹配首次 exec_command 返回；查询不重做原命令。通过当前工作流状态返回的真实 stdout_ref 读取保留输出。
- 在独立 second 目录执行绝对路径补丁与读取，显式 exec_command.workdir 执行 type 命令退出 0。后续相对 read_file 仍读取原 project 文件，默认目录未被跨目录命令改变。
- 额外命令实际退出 7，插件如实返回 exited/exit_code=7；未把观察成功当作命令成功。
- 状态显示根命令均退出、受管进程 0；仅结束本轮自身工作流，返回 ENDED。

## 异常输入与证据边界

一次 read_output 没有带上服务器返回的输出引用，以及一次 read_file 空路径，均收到客户端 isError 且没有 structuredContent。错误正文没有保留，不能补写具体错误码或宣称是哪一层拒绝；这两个输入没有被重放。之后使用工作流状态返回的真实输出引用及合法文件路径，状态、结果、文件和命令调用均正常。

只读复核近 150 条服务诊断没有找到这两次输入对应的 read_file/read_output INVALID_ARGUMENT 或 tool_raised 事件；缺少记录不能独立证明平台拒绝原因。服务端非法参数隔离的精确合同证据仍来自前一阶段认证本地 HTTP 验收。本轮只证明观察到客户端拒绝之后，同一工作流的合法调用仍可完成；没有绕过平台保护，也不宣称所有安全提示永久消除。

## 最终状态与证据

最后实际插件 server_info 仍为 0.1.12/58、health=healthy、诊断零丢失；所有活动资源、命令、受管进程、保留命令为 0，数据库仅有 234 个 ENDED。随后只读本机状态、任务和进程身份与该结果一致：运行和下次登录目标仍是 r3，无竞争实例。

本轮原始摘要为 `verification/recheck012-20261009/client-tunnel-acceptance.json`，最终只读快照 `post-client-live.json`。文件不含工作流令牌或运行密钥；隔离文件和历史证据保留。旧的本机切换回执没有覆盖，旧包没有重打包。本轮没有远端推送。
