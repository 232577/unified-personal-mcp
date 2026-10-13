# 限定范围静态安全审查

审查目标为当前源码中的 Unified HTTP 鉴权、工作流归属与生命周期、工具安全注解、coding 权限查询及 Git 查询、BF 任务与窗口归属、浏览器 origin 策略、管理型更新停机控制。此次采用 security-scan 技能的 source-backed 审查原则；未建立正式 host scan、未运行 capability preflight、无独立 baseline worker，因此不能视为完成 Codex Security 标准扫描或全仓库审计。没有访问真实凭据、BF 插件或外部服务，没有更改产品配置或停服务。

## 已实证并修复：只读 Git 查询启动仓库 helper

`coding_tools_mcp/server.py:2662-2684` 直接通过 `subprocess.run` 调用 Git，不经过 `exec_command` 权限分类、Landlock 包装或 OwnedJob。`git_status` 在 3099-3102 运行普通 `git status`，没有覆盖 `core.fsmonitor`；`git_diff` 在 3176-3182 运行普通 `git diff`，没有 `--no-ext-diff` 或 `--no-textconv`；`git_show` 在 3301-3316 仅禁用 external diff，仍需确认 textconv 行为。`personal_mcp/tool_policy.py:23-24` 把这些入口列为只读，`host.py:289-321` 因此允许只读工作流调用。

攻击前提是本地仓库 Git 配置/属性或其引用 helper 受到不可信内容影响，可信客户端随后运行查询。Git helper 执行会把只读查询变成代码执行或写入；在 Linux safe 模式，原 Git 子进程路径还未经过 exec 的 Landlock。此处不把设备所有者明确启用 full_control 本身当成漏洞，也不把同一安装中的可信客户端虚构成多个隔离租户。

运行审查代理已在 `tests/test_runtime_boundary_regressions.py::GitReadOnlyHelperTests` 的新建临时仓库实证：只写 fixture 的 helper-ran.txt，修复前 external diff、textconv、fsmonitor、diff clean filter 四项确实写 marker，定向结果为 4 failed / 1 passed；其后 root diff.submodule=diff 又触发子仓库 clean filter，新用例修复前 1 failed。没有在真实项目配置 helper，没有使用网络或真实数据。

实现已修复，最终源码独立只读复核确认：`server.py:2675-2730` 的公共 Git 执行层只对当次进程覆盖 core.fsmonitor、status.submoduleSummary、diff.submodule 和当前仓库配置的 filter.clean/smudge/process/required，不持久化改动配置。git_diff 禁 external diff/textconv 并限定 short 子模块形式，git_show 禁 external diff/textconv，git_blame 禁 textconv。status/diff 使用 ignore-submodules=dirty，避免进入子仓库工作树的独立 helper 配置；子模块 revision 变化仍呈现，存在 .gitmodules 时明确发布工作树修改不检查的 warning。源码的 Git ignore 查询为 check-ignore，不执行内容转换 helper，改为字节路径运输以保留中文/非 UTF-8 名称。

运行审查代理最终报告新增组 17 passed / 1 skipped / 10 subtests，包含配置原值未变、普通 diff 正确、子模块 revision 变化仍可见及 warning 可见。本报告独立核对了测试与修复源码，未重复执行该代理测试。跳过项为 POSIX 非 UTF-8 字节文件名；没有据此声称 Linux Landlock 获得本轮运行验证。

## 已确认契约问题：full_control 权限查询夸大 sensitive_env 许可

`CodingRuntime` 的 full_control 初始化使用 dangerous 上游能力，但在 `personal_mcp/coding.py:151-159` 再次过滤敏感变量及 loader/startup 注入变量。继承的 `Runtime.request_permissions`（`server.py:3398-3413`）对包括 sensitive_env 在内的所有请求返回 granted 和 dangerous 全许可信息。于是查询说允许，实际命令环境仍移除所需变量。

已按该建议修复：`personal_mcp/coding.py:56-87` 在模型可见文本与结构化结果中说明既有本地配置语义；sensitive_env 返回 denied / SENSITIVE_ENV_FILTER_ENABLED，其他 full_control 能力只返回 existing-full-control-policy，并明确 user_approval=false、platform_approval=false、过滤 enabled。请求参数中的敏感环境值不回显。safe/trusted 模式保持原有 unsupported 行为。此问题为接口正确性问题，不构成需要放松过滤的理由。

同轮验证：先运行新测试，确认原实现出现 2 failed / 2 passed（敏感变量误授予和模式误报均命中）；修复后 4 passed。随后运行 test_full_control_coding.py、test_coding.py、test_full_control_host.py、test_catalog_client_contract.py，共 28 passed，Ruff 与 Git diff 空白检查通过。测试使用项目内隔离夹具；沙箱模式下 Windows 严格路径解析出现 WinError 5 后，在经过审批的本机普通权限环境运行测试。

## 长期容量风险：去重行不会回收

发现时 `personal_mcp/operations.py:46-57` 的清理仅置空结果，`run` 在 133-134 按所有 owner 的累计行数达到 max_records 后拒绝新操作。旧的 ENDED/EXPIRED 工作流记录同样累计，因此长期使用可能永久进入 OPERATION_CAPACITY。

主代理已增加边界明确的回收：`host.py:133` 绑定内部 `WorkflowRegistry.closed_operation_owners`；`workflows.py:336-340` 只返回 ENDED/EXPIRED 身份；`operations.py:130-139` 在达到容量时回收这些身份的去重行并排除当前 owner。只读复核未见重放或锁反转：查询已有 receipt 早于回收；ENDED/EXPIRED 不可再 activate/resume/use；ACTIVE/CLOSING/CLEANUP_BLOCKED 和未知 owner 保留；registry 在 dispatch 和资源 close 前释放自身锁，没有持 registry.lock 再等待 operations DB 的已观察路径。晚到的完成回调只能 UPDATE 已删除行，不会复活回执或重新 dispatch。此处仅引用主代理报告的测试证据，未独立重跑容量测试。

## 工具注解语义

`catalog.py:93-99` 默认把所有非只读工具标成 destructive。begin 会通过 `WorkflowRegistry.expire`（`workflows.py:272,316-330`）维护其它空闲工作流；其安全前提由 idle gate、inflight、资源活动与未知状态阻断保证（228-260、`host.py:49-76`）。activate 对 RESERVED 新建本工作流资源，对 ACTIVE 只更新活动时间，但也先执行 expiry（336-379）；初始化失败会关闭部分本工作流资源（`host.py:31-47`）。因此它们不能声明只读。能否把 destructiveHint 改为 false，需要明确区分“只清理本次初始化资源”和“维护其它已空闲任务的短期状态”；本审查未修改注解。平台审批最终由平台决定，准确注解无法保证放行。

## 已观察到的有效控制与限制

- 配置只允许 127.0.0.1；HTTP POST 在解析 MCP 请求之前验证 bearer 与 Origin（config.py:89-92；server.py:4967-4973,5116-5128）。
- Unified 使用单一 local-owner 身份；同一安装为同一个信任域，不提供多个互不信任远程用户隔离。工作流 token 哈希、owner、revocation 与 generation 检查存在（workflows.py:93-106,167-215）。
- 新写入在 dispatch 前检查 pending native 操作；同 request_id 先查去重结果，unknown 不自动重放（host.py:293-319；operations.py:110-153）。
- BF 任务与窗口绑定使用不可猜测凭据；跨任务写窗口有持久归属检查（task_store.py:94-107,263-282）。浏览器 origin 使用规范化后的精确 allowlist（browser/policy.py:17-59）。
- 管理更新等待零工作流和已知零资源、以数据库写事务关闭停机竞态、校验任务 XML/用户/实例策略以及服务进程路径，未知状态拒绝（scripts/switch_managed_release.py:125-216）。
- 未完整审计 UIA/native renderer、浏览器 worker 全部请求处理、安装/打包供应链、OAuth 上游兼容路径、所有 patch 文件操作或 Linux 专属 sandbox 实现；未验证两设备实际部署或平台审批。

此次完整阅读的较小文件：personal_mcp/tool_policy.py、credentials.py、full_control.py、coding.py、protection.py、config.py、catalog.py、tool_presentation.py、operations.py、workflows.py、host.py、tunnel.py、personal_mcp/bf/bootstrap.py、bf_automation/tool_contracts.py、browser/policy.py、hybrid/policy.py、scripts/switch_managed_release.py。server.py、service.py、task_store.py、window_control.py 等仅核对上述相关路径，不计为全文件审查。没有把检索命中当作完整覆盖。

## 追加复核：源码升级路径

完整阅读 scripts/build_update.py、scripts/prepare_update.py、packaging/update.cmd、tests_personal/test_source_update.py。此次没有修改这些文件或独立重跑升级测试；实测由升级实现代理负责。

`prepare_update.py:18-42,57-82` 拒绝路径逃逸、大小写重复项、symlink/junction 与未列出的文件，并逐项核对 SHA-256。`116-167` 在写入前检查输入重叠、缺少关键模块、版本和依赖摘要、资源摘要，以及新目录必须是不存在的同级目录。`169-180` 只复制到新目录并替换新目录的 app，不触及旧安装；配置和资源二进制不在升级载荷白名单。该流程接受已解压目录，没有自行解压不可信 ZIP，因此未引入 ZIP 路径拼接入口。旧包及更新包同目录的符号链接在目录级检查中被拒绝。

第一次读取时发现缺少 required_update 检查：重算清单后的缺模块载荷可生成残缺新包。已通知实现代理；其增加 `123-127` 的三个模块 __init__ 与关键脚本/元数据前置检查，静态复核确认生效。实现代理报告包含缺模块用例的 17 项测试通过；本报告不把转述当作独立执行证据。

0.1.10、0.1.11、0.1.12 是声明支持的基线，但实际兼容还要求 requirements lock 与指定资源摘要完全匹配，不能仅凭版本字符串承诺升级可用。已要求实现代理实测真实基线；正常使用产生的 debug.log 或浏览器设备登记目录若改变包文件集，会被 exact manifest 检查拒绝，此时应使用完整、未改的原始解压包作为基线，而不能静默放松完整性。自带 manifest 证明内容一致性，不证明发布者身份；公开 ZIP 的外置摘要应从可信 Release 取得。此次未观察到新的安全阻断项。

最终冻结后的 --apply 流程亦只读复核：prepare 生成新同级包后必须本地输入 y 才调用 apply_prepared；候选独立 Python 进程以 --switch-only 再校验候选 manifest，导入该已校验候选代码，检查默认管理任务存在/归属/旧包路径与 config，然后调用原 switch_managed_release.py --run。该路径沿用严格零工作流、资源明确零值、任务 XML/身份、服务进程路径与回滚检查，不能借 --apply 绕开 preflight。升级回执错误文字经主代理增加的 failure_code 白名单处理，避免把任意异常的私密内容写入回执。实现代理报告升级组 21 passed 与 Ruff 通过；本审查没有执行真实切换。

其他最终复核结论：read_file 普通续页以物理整行构建，并保留 end_line/max_lines 换算后的范围，不越界续读；超长首行仍只能返回预览，并明确 first_line_exceeds_limit。operations 的终态回收与不可重新进入的凭据前提保持成立，没有观察到锁反转或副作用重放路径。父代理全套回归进行期间没有启动重复测试，也没有改动冻结产品源码。
