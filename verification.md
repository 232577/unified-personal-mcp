# 验证记录

## 0.1.14 远程桌面与真实宿主验收（2026-10-13）

- 本产品回归 **994 passed、1 skipped、10 subtests passed**，228.42 秒，退出 0；UI 模拟宿主 31 项与独立授权复核 15 项通过。实际运行包中的隔离窗口鼠标、Unicode 输入、快捷键、滚动、拖拽和二进制传输闭环 PASS。
- 本机已切换到 **0.1.14 / 63 接口**，真实插件确认隧道健康。用户在新聊天打开操控台，确认真实画面；独立浏览器观察到只读画面和 1566/1851 毫秒往返。退出后活动任务与资源为 0。自动刷新包含额外等待，不是连续视频。
- 完整包 9250 文件、86 源码模块、7 UI 文件；公开升级载荷 125 文件、562966 字节，SHA256 `3a2a181fc1cfd0c4815b1b9e9ad3b6240703ed4a86e3cf9e48909ff017b7c8f4`。包内文档为构建时快照，详见 [本轮复核](docs/reviews/2026-10-13/release-0.1.14-review.md)。
- 真实宿主的最大文件保存和业务桌面输入未做验收；对应功能通过本机真实隔离夹具验证。另一台设备仍离线，**未更新**。没有连接 Remote Desktop Commander 的设备，也未绕过平台安全检查。

## 0.1.13 实际更新与客户端验收（2026-10-13）

- 最终本产品验收 **795 passed、1 skipped、10 subtests passed**，247.27 秒；唯一跳过为 Windows 不支持的 POSIX 原始字节文件名用例。Ruff 与差异检查通过。
- 9237 文件完整候选、81 源码模块、112 文件源码升级载荷一致性校验通过；包内真实 HTTP/命令去重/凭据轮换/中文搜索/浏览器闭环 PASS。
- 本机管理任务已切换到 **0.1.13 / 58 工具**，LOCAL_READY；真实 Unified Personal MCP 插件的工作流、文件两页读取、命令执行与观察、结束全部通过。旧 0.1.12-r3 包保留用于回退。
- 公开升级 ZIP SHA256 `54ea043d8f526fae6573f62fa36f82dd6ead07f0d9a94e2042ec6a6bc62fbef6`，364601 字节，不含配置、密钥或第三方运行环境。包内本文为构建时历史快照，最终证据以本条及 [本轮复核](docs/reviews/2026-10-13/release-0.1.13-review.md) 为准。
- 另一台设备的 local MCP 隧道离线，用户暂时无法操作该电脑，**未更新远端设备**。本次也不声称上游通用套件、所有安全扫描或所有平台操作全通过；限制与上游套件 18 项失败分类均见本轮复核。

## 0.1.12 r3 客户端刷新与实际 OpenAI 隧道调用（2026-10-09）

- 在 ChatGPT 已安装的 Unified 设置页面点击“刷新工具”，刷新后客户端应用弹窗显示 Write 28 / Read 30，共 58 个工具，新增八个具名入口均已列出；权限未修改，未截图。
- 实际调用 Unified 插件 server_info 返回 0.1.12/58/完整描述符指纹，随后完成本轮新工作流创建、状态/激活、文件补丁/回读、命令退出 0、原 OperationStatus 结果匹配、保留输出、绝对路径及跨目录命令、预期退出 7、自有工作流 ENDED。客户端操作未由本地 HTTP 代替，也未重放先前被拒绝的请求。
- 两次异常输入得到客户端 isError，错误正文/精确拒绝层未保留；服务诊断没有对应工具错误事件，不能补写 INVALID_ARGUMENT 的云端处理证明。之后同一工作流合法调用正常，详细边界如实记录。
- 最终实际插件及只读本机状态一致：r3/0.1.12、58 工具、健康隧道、诊断零丢失、活动资源和保留命令为 0，仅 ENDED 234；服务、配置与运行包没有再修改。当前旧对话仍绑定原 50 项，新具名接口未在旧绑定中逐项云端调用；目录刷新与实际兼容接口隧道链已完成。
- 证据 `verification/recheck012-20261009/client-tunnel-acceptance.json`、`post-client-live.json`；[客户端隧道复核](docs/reviews/2026-10-09/client-tunnel-review.md)。未修改客户项目、依赖、截图占位或平台审批设置，未推送远端。

## 0.1.12 r3 本机续接与实际启用（2026-10-09）

- 修复登记后的状态读取失败回退、切换登记收据保存和恢复前 XML 守卫；本机严格零工作流 `--run` 不需要虚构工作流引用，也不创建临时任务。源码修正提交 `cb0b50da0b5a677f54c9d920de8ff99f4d46e3b9`，无远端推送。
- 本轮完整非截图回归 **746 passed，219.98 秒，退出 0**；切换专项 **30 passed，0.28 秒，退出 0**，包含真实临时任务启停与清理。日志及 JUnit 位于 `verification/recheck012-20261009/`，新短 basetemp 为 `.tmp/q12f1` 和 `.tmp/q12s1`，有限超时分别 900/300 秒。未运行真实截图或截图验收脚本。
- 因运行模块修改，构建独立 `release-private/UnifiedPersonalMCP-0.1.12-r3`，旧 r2 和 0.1.11 原样保留。构建退出 0；9233 清单文件、81 运行模块、ZIP 集合/每文件摘要/CRC/sidecar、私有文件排除通过。ZIP **486201037 字节**，SHA256 `060d2860b63d61179d66829e54d34eb9cbed7024cd3a3e48f08f38af73b60408`。包内认证 HTTP 23 次工具调用及实际目录 58 工具通过，自有工作流和隔离服务清理成功。
- 受控切换退出 0，34.195 秒，`LOCAL_READY`，没有回滚。现有 `UnifiedPersonalMCP-AutoConnect` 的运行路径和下次登录目标同为 r3 包内 pythonw；配置和 backend/control/tunnel 密钥未变，原 full_control 保留。旧 Unified 自有进程树已退出，未停止 BF 或其他项目，也未重启 Windows。
- 实际生产实例的隔离认证 HTTP 文件/命令/工作流链、非法参数后查询、退出码 0/7/0、原回执完整结果比较及跨目录访问通过；当前无活动工作流/资源/保留命令或临时任务。诊断完整目录与 HTTP 一致，格式 `complete-descriptors-v2`，指纹 `0fc6d288141596b54af89b33e74557054466b0298a8a1860a8295009224dfbe4`，监督状态 healthy，隧道自身 `/healthz`、`/readyz` HTTP 200；调用后全包再校验通过。
- 证据 `final-receipt.json`、`live-http-acceptance/result.json`、`switch-live/result.json`，均在本轮目录内。命令、所有权与阶段详情见 [本机续接复核](docs/reviews/2026-10-09/local-activation-review.md)。**ChatGPT 工具目录刷新后的实际隧道调用仍待验收**；本机就绪不能代替平台放行，未重放此前被明确拒绝的请求。

## 0.1.12 最终 r2 运行包与维护修正（2026-10-09，切换前）

- 开工 HEAD `b828062196205564c052bb2b5e6c2be2f4b65316`。真实生产为 0.1.11、50 工具，归属当前用户的 `UnifiedPersonalMCP-AutoConnect`；源码和项目版本现为 0.1.12，完整候选目录 58 工具。
- 本轮修正 COM 内层 FILE_NOT_FOUND 识别和初始化引用配对；新增受控一次性切换脚本，核对具体任务、XML、用户、配置和工作流空闲状态，保留旧包回退。没有任意 PID 强杀或 Windows 重启。
- 最终非截图全套 **734 passed，249.83 秒，退出 0**。命令 `.venv-host\Scripts\python.exe -X utf8 -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=verification/release-012-r2-final --junitxml=verification/release-012-r2-final.xml`；原始日志同前缀 `.log`，工具命令 `zV49D1ukgHgc9AnGtYtEPlAX`。其中新增维护专项 21 项；独立真计划任务启停、冲突拒绝和删除验证通过。
- 修正版运行包 `release-private/UnifiedPersonalMCP-0.1.12-r2` 构建退出 0，命令 `wmQ5NVSzrsb9-UWKmEpbSsaE`；初版目录未覆盖，不能将初版误作最终包。
- r2 完整性 PASS：9233 个清单文件，81 个源码模块文件逐一匹配，ZIP CRC/文件集合/每文件 SHA256 全部核对，指定私有配置/密钥/状态文件排除。ZIP 486199600 字节，SHA256 `3600c61d64f82b75bba2b76ce6cbb57d200be79f93354db0a1cd6ff832d13dfa`。报告 `verification/release-012-r2-integrity.json`。
- 实际使用 r2 包内解释器和模块，12 次认证本地 HTTP 操作 PASS，包含工作流、文件补丁/读取、命令退出/观察/账本及清理，未使用生产隧道。报告 `verification/release-012-r2-portable-runtime/result.json`；完整性与实际包运行串行命令 `oM8y3_e-B9IjUb8HnABrb4IZ` 退出 0。
- 旧 0.1.11 回退包 9226 文件完整性检查通过，未覆盖。Ruff 和 `git diff --check` 通过。原生测试的历史失败和清理记录保留，详见 `docs/reviews/2026-10-09/release-0.1.12-review.md`。
- 打包内 verification.md 是构建时的历史快照；本仓库此条和后续实际切换记录是新的证据，不改写已校验的包。切换后实际插件调用另行记录，切换前不宣称生产已更新。未执行截图或本轮依赖安全审计。

## 开发分支：OpenAI 客户端表现与具名工作流接口（2026-10-09，未部署）

- 开工 HEAD `53ce8c928db98e0a8d7d82040a3296e6ef2be410`，分支 `feat/mcp-diagnostics-20261009`，工作树干净。全部开发机操作经 Unified Personal MCP；本轮未修改 software-factory-next、应用审批设置或生产服务。
- 所有公布工具增加一致的人类可读 title/annotations.title；根据真实安装范围说明路径能力，移除不接收路径参数的工具上无关的完全控制/workdir 说明。参数来源、当前秘密凭据、非秘密引用、真实代次、原请求查询与新请求 ID 的用途分别说明。标题是 MCP 可选字段，本项目新增其一致性门槛；字符串 enum 增加显式类型不改变合法输入集合。
- 工作流 begin/activate/resume/end/release_idle 分别增加固定 schema 的具名工具，完整目录由 53 增至 58。旧 UnifiedTask 仍公布为兼容入口。新旧入口共享 Registry、权限与回执；状态变更工具不标为只读，不接受 action、principal、approved 或任意方法名注入。
- 相关基线 40 项通过。表现测试先修正测试夹具目录冲突及 SDK 字段弃用，再取得 16 项失败、1 项原行为通过的有效红灯；实现后连同相关基线 57 项通过。具名动作 10 项先失败、实现后 10 项通过。本轮新增测试共 27 项。
- 首次完整非截图套件 **711 passed、2 failed，235.41 秒，退出 1**。两项旧精确注解断言因新增 title 失败；修正为同时检查原四个风险值及具体标题，保留全部原行为断言。原始失败输出 `verification/presentation-20261009-final.log` 未覆盖。
- 最终完整非截图套件 **713 passed，227.23 秒，退出 0**。命令 `.venv-host\Scripts\python.exe -X utf8 -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=verification/presentation-20261009-final2 --junitxml=verification/presentation-20261009-final2.xml`；日志 `verification/presentation-20261009-final2.log`；本轮命令句柄 `hySZaiY5FVJ6bMMf-a71ajye` 的退出码已独立核验。
- Inspector **2.7.0 / Node v22.23.2**：**18 项隔离本地 HTTP 检查 PASS，58 工具**，父命令 `ewlyJ8iWS10AjogbKaM7Z1nc` 退出 0 已核验。报告 `verification/presentation-20261009-inspector/result.json`；新动作实际调用、空闲释放预期拒绝、旧凭据拒绝与跨新旧入口重复恢复均包括在内。沿用 legacy 协议，不是所有协议版本认证，也不使用生产隧道。
- 从上述隔离服务的实际目录快照导出 `docs/reviews/2026-10-09/tool-interface-report.json`，58 个描述符经已安装 MCP Tool 模型解析并核对标题一致。隔离 trusted 目录指纹为 `38056a629b3a1591db2145b6c5b7fb01c2c000eaca9e658a07faeab55c2acc4f`，不得与不同配置的生产目录直接比较。既有逐工具错误合同与隔离测试仍覆盖 trusted/full_control 两种完整配置。
- Ruff 与 Git 差异空白检查通过；操作说明 `docs/personal/OpenAI-tool-interface.md`，实施者自查 `docs/reviews/2026-10-09/tool-interface-review.md`。不是独立安全审计或 OpenAI 审核通过证明。说明书保留人工截图占位，并将部署后的模型选工具用例明确标为待验收。
- 未运行真实截图或桌面验收脚本，未部署、构建安装包、审计依赖或触发授权弹窗。request_permissions 和 Notification 的能力边界如实说明，没有新增伪造批准字段、noauth/OAuth 声明或绕过平台保护的机制。其他混合动作接口仍保留兼容，未宣称全工具接口迁移完成；平台安全拦截消除仍需上线后真实验证。

## 开发分支：逐工具失败隔离与命令观察（2026-10-09，未部署）

- 开工 HEAD `fe743737a907bc2978f0340f08090706a5d2cb7b`，分支 `feat/mcp-diagnostics-20261009`，工作树干净；本轮经 Unified Personal MCP 操作插件源码，不修改 software-factory-next。源码版本号仍为 0.1.11，候选目录增至 53 工具，不是已发布安装包。
- 逐工具测试复现 47 个上游工具的统一层参数/授权错误不符合公布的 outputSchema。新增明确且有限的错误分支，保留原成功/原生错误分支；不伪造成功数据，不降低风险注解。第一次失败列表见 `verification/isolation-20261009-all-tools-first.log`。
- 已完成或已失败的请求回执以及参数冲突，原来会被同工作流其他待决原生工作遮挡。现在仅真正的新派发经过待决保护，且在新增回执前执行；原有写权限/归属检查保留，未知副作用不重做。4 个回归先失败，同组及相关账本/并发控制 26 项通过。
- 新增 command_status 独立只读观察，返回真实根命令状态、退出码、输出引用；不写 stdin、不消费游标、不续租、不取消。13 项回归先失败，覆盖真实运行/退出 0 和 7、忙锁、只读归属、撤销凭据及拒绝写入参数。未知句柄不代表命令未执行。
- 实际搜索还触发与平台拒绝不同的 GBK INTERNAL_ERROR。真实固定版本 rg 与中文路径/内容的回归先失败，明确 UTF-8 解码后通过。独立提交 `086c0708c026a879a377b3d9cf8e994b0e59ac2f`，逻辑修复为一行；Git 行尾差异记录见本轮复核文件。
- 第一次基线命令使用默认 30 秒限时，实际 timeout/退出 15，未计为通过。最终命令明确设置有限的 600000ms，未删除测试或降低断言。
- 最终非截图套件：**686 passed，205.92 秒，退出 0**。命令 `.venv-host\Scripts\python.exe -X utf8 -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=verification/isolation-20261009-final-v2 --junitxml=verification/isolation-20261009-final-v2.xml`；原始日志 `verification/isolation-20261009-final-v2.log`；命令句柄 `zjLPxOj_BEujGY5NTvnFUDv0`。
- 从实际测试产物导出 `docs/reviews/2026-10-09/tool-failure-matrix.json`：trusted 与 full_control 均启用完整浏览器工具目录，各 53 项非法输入拒绝、错误输出 schema 与工作流保留检查通过，共 106 条工具/模式记录。先前最小 full_control 夹具只有 43 工具，首次导出因此失败；修正测试配置并重新全套验证，保留旧报告。矩阵不代表所有工具的真实业务动作或平台放行。
- 已读取 Inspector 2.7.0 / Node v22.23.2 的持久化报告：**17 项隔离本地 HTTP 检查 PASS，53 工具**。包含非法 read_file 输入预期拒绝后 command_status 仍能观察原命令。报告 `verification/isolation-20261009-inspector/result.json`；legacy 协议，未使用生产隧道。父命令句柄后续过期，未独立取回其退出码，不把报告 PASS 改述为已核验父命令退出 0。
- Ruff 与 Git 差异空白检查通过。说明书 `docs/personal/工具失败隔离.md`，实施者自查 `docs/reviews/2026-10-09/tool-failure-isolation-review.md`。未运行真实截图、桌面验收脚本、生产热替换、依赖审计、安装包构建；真实截图测试源码保留且不计为通过。
- 这些修复减少可证明的本地接口故障和连带阻塞。平台拒绝请求本身没有被重新编码、改名或换通道重放，不能宣称平台安全拦截的原因已查明或影响完全消除。上线真实调用验收仍为发布阻塞。

## 开发分支：工具契约与服务端授权分离（2026-10-09，未部署）

- 用户报告的安全拦截保留为发布阻塞；本轮修复插件中已证明的耦合与契约问题，不宣称已查明平台拒绝原因。开工 HEAD `7841c3ea539de8e5d345f1c9207510fd2c90d33b`，分支 `feat/mcp-diagnostics-20261009`，工作树干净。
- 运行时写权限与请求去重不再依赖对外 readOnlyHint，使用 `personal_mcp/tool_policy.py` 中明确登记的策略。保留原有角色、凭据、所有权与 SearchSession 任务私有搜索控制规则；没有新增权限、接口或自动重试。
- 发布目录检查名称、描述、合法 schema、显式布尔风险标注和已登记的读写/部分效果约定，无效目录在 Runtime 资源初始化之前拒绝。修正 write_stdin、Clipboard 和 full_control 文件工具的潜在效果标注；这些是更保守的声明，不是获得平台放行的开关。
- 本轮非截图基线 **628 passed，106.54 秒，退出 0**。首轮新增回归 20 项失败；风险标注回归再发现 11 项失败；资源初始化顺序问题单独先失败后修复。全部原始输出保存在 `verification/contract-20261009-*.log`，没有覆盖失败证据。
- 最终非截图回归 **662 passed，172.13 秒，退出 0**。命令 `.venv-host\Scripts\python.exe -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=verification/contract-20261009-full --junitxml=verification/contract-20261009-full.xml`；命令句柄 `aIRtXnO7tx81qwLIQTh34nRu`；原始日志 `verification/contract-20261009-full.log`。
- Inspector **2.7.0 / Node v22.23.2** 对隔离本地 HTTP 服务的 **14 项检查通过，52 工具**；命令 `HSstt42sF0Q-5waz_UnW2aJq` 退出 0；报告 `verification/contract-20261009-inspector/result.json`。使用 legacy 协议，不连接生产隧道，不代表平台端验收通过。
- Ruff 与 `git diff --check` 通过。复核记录 `docs/reviews/2026-10-09/contract-authorization-review.md`；属于实施者自查，非独立安全审计。
- 未执行真实截图、生产切换、已被拒绝的热替换预检重放、依赖升级或审计、安装包构建、平台侧拦截消除验收。源码版本号仍为 0.1.11，当前识别以提交和目录指纹为准，不能当作正式新版安装包。业务工程 `software-factory-next` 未修改。

## 开发分支：独立工作流查询（2026-10-09，未部署）

- 在诊断提交 `01fbbf0fe9136ce704551320f547ebd0a8e80466` 之后增加 `workflow_list` 与 `workflow_status`，开发目录总数 52；旧 50 工具保持兼容，旧 UnifiedTask 不伪装为只读。源码包版本号仍为 0.1.11，当前交付以分支、提交和目录指纹识别，不作为正式新版安装包。
- 16 个查询测试先全部失败（缺少新入口），实现后同组 16 项通过，9.65 秒。覆盖真实认证 HTTP 目录/调用、数据库不变、长写入期间查询、所有者隔离、旧凭据拒绝、真实受管命令清单。
- 本轮最终非截图套件 **628 项通过，102.81 秒，退出 0**：`.venv-host\Scripts\python.exe -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=verification/diag-20261009-queries-full --junitxml=verification/diag-20261009-queries-full.xml`。原始日志 `verification/diag-20261009-queries-full.log`；Unified 命令 `MwoWp8l5zCz26FitTn_baNXV`。
- 使用已安装的 Inspector 2.7.0 / Node v22.23.2 对隔离本地 HTTP 服务执行 14 项协议冒烟检查，报告 PASS，发现 52 工具；未使用生产隧道。包含未认证预期拒绝、只读查询、命令与结果账本、凭据轮换/重复恢复、旧凭据预期拒绝和正常结束。命令 `vkIBn_ssoWw62R_RUhY-3pHl` 退出 0，结果 `verification/diag-20261009-inspector/result.json`。
- Inspector 使用 legacy 协议入口，不代表全部 MCP 版本一致性认证。隔离 trusted 配置的目录指纹为 `6cb6afe532af8b7856ef31f0f5f8a0558a477bd31e5a26ce4c2d9b5028ce5351`，不得直接与其他权限/资源配置的生产目录散列比较。
- Ruff 和差异检查通过。复核记录为 `docs/reviews/2026-10-09/workflow-query-review.md`，迁移说明和人工截图占位见操作说明。未执行真实截图测试、依赖审计、安装包构建、生产切换或平台安全拦截消除验收。

## 开发分支：本地调用诊断（2026-10-09，未部署）

- 基线 `128678c49e03ee2f66f9d051bae8d0dda7fd1790`；实际插件源码位于 `D:\run\unified-personal-mcp`，在 `feat/mcp-diagnostics-20261009` 开发。业务工程未修改，生产隧道未重启。
- 新增有界私有目录日志、HTTP 与工具阶段关联、目录实际快照；日志失败可见但不改变原执行结果或重放。完整描述符指纹使用 `complete-descriptors-v2`，与旧部分字段算法明确区分。
- 新增回归先失败再实现。旧“描述不改变指纹”契约已按本次需求改为分别验证描述和 schema 都改变指纹；原先失败输出保留。
- 本轮非截图基线 596 项通过；诊断能力最终复测 **612 项通过，95.11 秒，退出 0**。命令为 `.venv-host\Scripts\python.exe -m pytest -q tests_personal tests_bf --ignore=tests_bf/browser/test_frame_privacy.py --basetemp=verification/diag-20261009-p0-final --junitxml=verification/diag-20261009-p0-final.xml`；原始日志为 `verification/diag-20261009-p0-final.log`。
- Ruff 与 `git diff --check` 实际通过。复核为实施者自查，详见 `docs/reviews/2026-10-09/diagnostics-review.md`；操作说明见 `docs/personal/调用诊断与工具契约.md`。
- 按用户要求未执行唯一真实截图测试 `tests_bf/browser/test_frame_privacy.py`，保留原测试且不记为通过；未执行截图验收、依赖审计、打包、部署或新版平台端到端验收。不能宣称安全拦截已消除。

以下为原 0.1.11 / 50 工具基线及历史验证。2026-10-09 的开发分支变化以上方新条目为准；生产运行状态以实际 server_info 为准。

## 0.1.11 隧道代理与轮询状态（2026-10-08）

- 实际插件返回 tunnel_client_not_seen，本机却显示 healthy；日志确认对 api.openai.com 的直连持续超时。同机无密钥连接测试经现有系统代理返回HTTP401，而直连超时，区分了可达性与授权。
- 启动只采样一次已启用的显式 WinInet HTTP/HTTPS 代理，写入 control_plane.http_proxy。没有修改系统网络、代理配置、PAC、本地MCP路由或key；代理凭据、路径、查询、片段和无效端点拒绝加入配置。
- 连接指纹包含密钥与有效代理；显式重新连接时，变化只重建受管隧道子进程，保留业务后台。
- 本地 readyz=200 但当前实例已记录轮询失败时，状态降级为固定 TUNNEL_POLL_FAILED；仅使用日志布尔状态和时刻，不返回原始错误载荷。读取上限64KB，按实例保留已观察状态，日志滚动或缺失不能自行清除失败。
- 新增代理和轮询回归先失败后修复；完整基线597项通过（88.92秒），随后最后状态缓存边界修复的78项定向复测通过。Ruff/diff检查通过。跨领域只读审查的滚动状态问题已修复。
- 成品、隔离启动、CI及实际启用结果另存本机verification。当前生产0.1.8未修改，运行版本以server_info为准；没有将本地ready或代理HTTP401当作实际插件验收。

## 0.1.10 启动失败可诊断（2026-09-28）

- 用户报告另一台干净 Windows 10 完整解压后双击无窗口；本机不能代替目标设备复现，因此没有将依赖猜测当作已确认根因。
- 已确认旧 Launcher 使用 pythonw 启动，内部非零退出仅返回退出码，没有界面提示或日志。真实子进程回归先复现，再改为隐藏 python.exe、双管道并行有界读取、UTF-8日志及失败提示；正常成功退出不弹窗。
- 移除继承的 Python/Tk 定位变量，使用隔离解释器模式；命令参数中的空格、引号、中文保留。修复打包入口忽略 main 返回值导致失败被记为成功的问题。
- 新增“启动诊断.cmd”和独立标准库脚本，记录系统、架构、包完整性、Python/Tk/首屏导入结果；不读取设备配置或key，不启动服务和云连接。诊断入口可在主启动器失效时保留窗口与退出码。
- 依赖审查核对首屏实际导入链，未发现明确遗漏的第三方 DLL。包内有 Python、Tcl/Tk、pywin32与 VC DLL；不能据此断言另一台机器已经具备全部系统条件。
- 全量复测549项通过（89.59秒）；最终启动专项13项通过，包含新增的文件集合检查。最初全量受本机 WinError10055 影响的5项已单独通过后再全量复测。Ruff/diff检查通过。
- 新包与0.1.9差分补丁的验收结果保存在本机 verification；目标Win10的实际故障仍待新启动器日志/诊断报告确认。相关官方依据：[Python Windows支持](https://docs.python.org/3.13/using/windows.html)、[Windows UCRT](https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment?view=msvc-170)。

## 0.1.9 新设备完整运行包（2026-09-28）

- 首次没有配置时进入双字段窗口，自动建立本机 projects/default 和 data；已有配置仍进入旧控制台，不自动覆盖。
- 密钥默认遮挡，保存后清空输入，不进入命令参数或配置 JSON；明文运行密钥文件仅在已收紧权限的本机私有目录中保存。
- 配置创建采用路径/安装锁与原子排他提交，配置提交失败回滚密钥；并发创建和既有文件保护有回归覆盖。旧文件导入和环境变量配置路径保留。
- 运行包通过完整性及版本校验后，首次向导使用包内 pythonw 启动独立后台服务；登录自动连接可取消，登记失败与配置保存成功分别反馈。实际连接状态由既有轮询确认。
- 完整 tests_personal + tests_bf：537 passed，86.62 秒；Ruff 和 git diff --check 通过。独立正确性审查发现的版本夹具问题已修正后重测。
- BF 原生窗口快照检查了双字段、密钥遮挡、权限选项、自动登录选项及按钮布局；隔离预览窗口正常关闭，没有填写真实凭据。
- 搬迁、成品首次配置与后台启动证据另存本机 verification。完整包包含 Python、Chromium、rg、隧道客户端和依赖；WebView2 业务应用仍需要目标机的 WebView2 Runtime。不能把同机隔离验证描述为另一台物理电脑或新云端密钥实连验证。

本轮没有重启生产服务，也未更改本机登录启动目标。交付的运行包不含设备配置、key、业务项目或浏览器用户资料；目标设备按随包“使用前先看.txt”配置自己的隧道，客户端插件还需绑定该隧道。

## 0.1.8 全工具开发检查（2026-09-23）

- 工作流：等待其他会话清理后重新检查预约期限，过期预约不启动；数据库与健康快照状态一致。
- 搜索：耗时清理移出全局管理锁，另一项目可继续读取；清理完成前保留槽位，失败保留资源供重试。
- 桌面、浏览器及 WebView2：解除任务结束钩子与启动登记之间的锁反转，结束期间拒绝新资源；未完成的应用启动阻止误报结束，清理或数据库失败后仍可重试。
- 服务与配置：串行启停；维护失败反映为降级；保存配置时保护旧、新安装目录，提交失败恢复旧密钥。提交成功后的私有备份删除失败不再误报保存失败。
- 隧道：健康检查拒绝跳转；显式重试检测密钥变化。新密钥缺失、无效或旧进程清理失败时保留资源并报告固定错误码，不能随后误报健康。自启动核对注册回执，保留外部并发修改。
- 客户端：当前已加载插件的 UnifiedTask 类型仅剩 action，实际 begin 返回 INVALID_ARGUMENT；相同参数经本机认证 HTTP 成功并正常结束。每个 oneOf 分支现声明完整参数并独立禁止未知字段，七种动作回归通过，原服务端约束保留。组合语义参考 [JSON Schema 官方文档](https://json-schema.org/understanding-json-schema/reference/combining)；这是客户端兼容修复，不将旧合法 schema 描述为无效。
- 输出轮询：明确每次新 write_stdin 轮询使用新 request_id，丢失响应重试才复用原 ID；修正使用固定 ID 的测试，保留防重复消费机制。
- 三个独立领域审查后交叉复核，修复审查发现的隧道假健康和配置保存假失败；新增回归均先失败后修复。完整 tests_personal + tests_bf：518 passed，78.22 秒；Ruff 与 git diff --check 通过。
- Inspector 2.7.0 / Node 22.23.2 对隔离 0.1.8 服务完成 12 次调用，覆盖认证拒绝、50 工具发现、命令结果查询、凭据恢复重放、旧凭据拒绝和结束，全部通过；隔离服务已停止。

当前生产服务仍为 0.1.6，保持业务任务运行。成品验证和下次登录注册另存本机 verification；运行版本以实际 server_info 为准。0.1.8 经真实 OpenAI 隧道的新参数验收，需要先切换新版并刷新客户端目录；本地契约与 Inspector 成功不替代这一步，也不表示已消除所有潜在故障。

## 0.1.7 遗留子进程诊断（2026-09-23）

- 真实复现启动命令退出后其子进程继续留在 Windows Job：根命令数为 0，空闲释放仍正确返回 running_commands。资源存在与否以归属进程组为依据，不以父进程链或空闲时长推断。
- UnifiedTask status 新增 job_processes，区分仍运行的命令根进程和其他 Job 成员；server_info 新增 coding_job_active_processes，原 commands 语义保持兼容。只返回诊断 PID/计数，不暴露命令行，不新增按 PID 强杀接口。
- 查询失败、锁忙、命令已启动但尚未登记时，明确返回 unavailable/null；汇总保留先前缓存并标记 stale，不能把未知当作空闲。
- 5 项新增回归经过先失败后修复，覆盖真实后代存活、工作流隔离、查询异常、锁忙和启动登记竞态。独立审查发现的登记竞态已修正并再次复核，无遗留可操作问题。
- 完整 tests_personal + tests_bf：477 passed，73.53 秒；Ruff 与 git diff --check 通过。资源释放/自动回收策略没有变化。本机已通过既有 0.1.6 resume/end 正常处理目标工作流，保留其他活动项目和服务。
- 当前已安装插件目录已刷新并出现 resume/OperationStatus，但正在执行的任务可能保留此前加载的签名；目录刷新不能替代当前任务新工具实际调用验收。0.1.7 的成品包和启用结果另存本机 verification，以实际运行版本为准。

## 0.1.6 恢复与并行开发（2026-09-22）

- 独立隧道监督覆盖启动时暂未就绪、子进程退出后的退避恢复、单次连接尝试与终止清理。需要处理的配置错误可通过配置窗口或本机认证接口重新连接，不重启业务进程。
- BF 连接使用独立代次，恢复连接保留原资源归属，已经分发的操作不自动重做。审查发现的原生任务结果未知时提前清理风险已经修复：只有旧执行线程实际结束后，才解除相关所有者的清理屏障。
- OperationStatus 查询原请求的运行、完成、失败或未知状态；晚到结果继续入库。结果大小、保留期限和总量有界，结果到期后仍保留请求标识防止误重放；历史记录迁移保留查询宽限期。
- UnifiedTask resume 显式轮换凭证，保留当前工作流及受管进程，旧凭证立即失效；同一恢复请求在短期内可幂等重试。使用 Windows DPAPI 保存恢复响应，不把明文工作流凭证落库。
- 慢项目初始化移出全局注册锁；状态、输出读取和取消可在普通写入等候期间通行。真实长命令验证了响应性、进程保留及输出游标的一致性；同目录写入和共享前台输入仍需协调。
- 自启动入口随便携包交付，校验文件清单、摘要、指定版本和隔离启动后再修改登录任务，保留原任务备份。正在运行版本与下次登录版本分别显示；更新期间外部修改任务时不覆盖该修改。
- MCP Inspector 固定为 2.7.0，在隔离 HTTP 服务上通过认证握手、拒绝未认证访问、50 工具发现、代码执行、结果查询、凭证恢复、重复恢复、旧凭证拒绝及结束清理。它是独立客户端冒烟验证，不等同完整协议一致性认证。
- 本轮没有停止当前生产服务、原 BF/coding 服务或业务项目。新版经实际 OpenAI 隧道调用新增工具的验收，安排在用户授权的下一次登录切换之后；源码、包和计划任务版本不能替代实际 server_info 证据。

完整 `tests_personal + tests_bf` 回归：472 passed，69.72 秒；Ruff 和补丁格式检查通过。额外覆盖桌面操作超时后仍在执行时拒绝同工作流新写入，同时保留状态、输出和取消通道；其他工作流继续运行，原操作实际结束后才恢复写入。原始证据保存在本机 verification，包含独立审查、Inspector、回归和随后针对成品包执行的检查，不公开设备配置或凭证。公共 CI 的实际结果见 [Windows unit checks](https://github.com/232577/unified-personal-mcp/actions/workflows/windows.yml)。本记录随构建输入发布，不能替代运行包检查结果或当前设备的实际服务状态。

## 0.1.3 后台控制台（2026-09-22）

- 复现条件为与登录启动相同的无控制台 pythonw 父进程。修复前，桌面辅助 PowerShell 创建了非零控制台窗口句柄；修复后返回 0，命令输出及退出码保留。
- 仅对 Windows-MCP PowerShell helper 的模块级 subprocess 安装现有后台策略，也覆盖其超时清理命令；不修改全局 subprocess 或第三方安装文件，不设置会隐藏 GUI 的 SW_HIDE。
- 完整 `tests_personal + tests_bf` 回归：348 passed，39.09 秒；Ruff 及补丁格式检查通过。包含现有真实 GUI 操作、应用归属和结束清理验证。
- 编码命令、浏览器 worker 和受管理应用原本已使用隐藏控制台启动。显式要求另开窗口的自定义脚本仍需由调用方选择后台启动方式。
- 生效需要运行新版服务，源码更新不会改变已加载的旧进程；当前部署版本以实际 server_info 为准。

## 历史自动启动验证（0.1.3，2026-09-22）

新增 `scripts/install-autostart.ps1`，主程序无需升级。脚本语法检查及独立复核通过；使用 0.1.1 完整便携包注册隔离计划任务，直接后台启动随包 Python 服务，认证 HTTP 返回 49 个工具及 full_control。正式配置只注册登录触发器，没有立即启动、重启或切换生产服务。

计划任务直接管理服务进程；最初用外层 EXE 的验证发现停止计划任务会留下解释器子进程，因此改为直接运行随包 pythonw 与相同入口。实际测试确认启动长命令后停止隔离计划任务，相关子进程随 Job 释放退出。

当时未实际重启电脑或退出 Windows 会话，也未用第二条真实云隧道验证启动；自动连接参数、登录触发条件、启动失败重试和现有本机密钥引用已经核对。0.1.3 不包含隧道进程运行途中退出的自动恢复；该能力在上方 0.1.6 验证中单独说明。

## 0.1.2 多项目并行（2026-09-22）

- 完整专项回归 `tests_personal + tests_bf`：344 passed，43.96 秒；Ruff 与补丁格式检查通过。
- 真实进程验证：8 个独立项目同时执行，工作目录和进程归属一致；结束 A 后其余 7 个继续运行。命令活跃时超过空闲期也保留；命令退出后可回收旧会话并重新建立写入工作流。
- 便携包认证 HTTP 验证：同时运行 8 个独立 Chromium 会话、保留 16 次搜索，结束一个工作流后其余 7 个浏览器仍可观察，最后全部清理。server_info 报告版本 0.1.2、full_control、49 个工具及实际配置额度。
- 空闲回收回归覆盖旧数据库迁移、失去令牌后的所有者引用、同目录及父子目录冲突、在途调用、运行资源、清理失败、探测失败、跨所有者拒绝，以及激活与回收的并发竞态。32 个兄弟目录的工作流同时进入验证通过。
- 默认浏览器额度从 4 调为 8、WebView2 受管理实例从 2 调为 4、搜索从 4 调为 16；均可在主配置窗口调整。每工作流 16 条命令、单个前台输入租约保持原样。
- WebView2 新上限已通过配置和管理器回归，尚未对 4 个真实 WebView2 应用做同时运行验收。应用 profile 中的旧 `hybrid.max_instances=2` 是兼容字段，服务总额度由主配置决定。
- 本轮使用独立配置和测试数据，运行中的个人隧道服务未切换。实际启用版本以当前插件 server_info 为准。

## 历史基线（0.1.0 / 0.1.1）

验证日期：2026-09-21。Windows x64，独立 CPython 3.13.14 环境。全部真实操作使用项目内受控数据；旧服务保持运行。

| 检查 | 结果 |
| --- | --- |
| 统一程序回归 tests_personal + tests_bf | 277 passed，27.62 秒；使用项目内短临时路径 |
| App 启动权限收紧后的定向回归 | 10 passed |
| Python 静态检查 | 通过 |
| 认证 HTTP 接口 | 49 个工具；认证、工作流隔离与结束清理通过 |
| Chromium | 四个独立会话；表单、保存、存储隔离、上传下载摘要与截图通过 |
| WebView2 | 两个受管理实例、三轮完整操作；原生另存为与实际导出文件摘要通过 |
| 窗口遮挡及最小化 | 三轮检查；前台和最小化状态保留。暂存的最小化截图不作为新鲜业务画面证明 |
| 附加已有应用 | 默认解析器验证进程与端点归属；会话及工作流结束后原应用仍运行 |
| 搬迁后的完整运行包 | 含空格的新目录，随包 Python、rg 和 Chromium；49 工具、命令重试不重复执行、搜索、浏览器及清理通过 |
| 搬迁后的 EXE 配置窗口 | 检查、启动、隧道占用拒绝、停止、关闭通过；所有本次启动的程序退出 |
| 当前任务经新插件实连 | 远程写入并编译代码；两个桌面程序、两个 Chromium 会话操作与保存、文件读取、截图通过 |
| 当前任务的隔离与清理 | 跨工作流访问拒绝；结束 A 后 B 仍可操作；两组均清理完成；搜索释放连续三轮通过 |
| 最终程序实连复测 | 重启更新后的便携程序，通过新插件修改 WinForms 输入框；结构化快照、真实保存文件及截图内容一致 |

## 0.1.1 完全控制模式

由机主显式选择 full_control，safe/trusted 的项目边界保持原样。回归覆盖外部绝对与父目录相对路径的读写、补丁、列表、搜索、图片，以及外部脚本与命令目录；两个任务的默认目录、命令 ID 和进程清理保持独立。外部项目可以作为默认项目，项目规则读取会在盘根终止。

新版便携程序使用认证 HTTP 接口完成了配置窗口读写一致性、49 个工具发现、跨目录文件增改读取、共享 Node 构建脚本语法检查、绝对命令目录及结束 A 后 B 继续工作的验证。共享脚本检查没有重新冻结任何业务项目。当前安装是否已切换到该模式，以 server_info 返回的 version、permission_mode 和 absolute_paths_allowed 为准。

独立审查发现显式工具链环境变量被重复过滤的问题，新增真实子进程测试先复现，再修复。RUSTUP_HOME、CARGO_HOME 和 PIP_CACHE_DIR 等显式非敏感目录得到保留；宿主默认环境仍仅继承 core，敏感变量仍过滤。

完全控制授予当前 Windows 用户范围内的文件和命令访问，不意味着项目之间的共享文件、端口或数据库自动隔离。浏览器、进程归属与默认项目租约仍保留。

## 已发现并修复的问题

首次便携包遗漏服务端额外依赖，搬迁实测出现 cryptography 导入失败。锁定完整依赖闭包后重新构建，真实 HTTP、搜索、浏览器与 GUI 验证通过。配置窗口反馈区域裁剪、Windows 过滤环境缺少 SystemDrive、WebView2 版本格式、受管理搜索进程和应用启动归属等问题已有回归覆盖。

最终独立审查发现四项故障恢复问题，新增五项测试先复现再修复：浏览器清理保留资源归属并允许重试；最后一次状态写入失败不阻止安装锁释放；失败的 WebView2 启动在浏览器清理之后回收；搜索可独立 release，释放保留结果和名额。完整回归与静态检查通过。

当前客户端实测发现 WinForms 输入框的结构化快照可能返回初始文本。新增真实窗口回归先复现，再通过读取当前控件文本修复，并保持密码框内容屏蔽。更新便携程序后再次通过新插件验证输入、快照、保存与截图。

公开仓库配置 Windows CI，每次推送执行专项回归与静态检查；执行结果见 [GitHub Actions](https://github.com/232577/unified-personal-mcp/actions/workflows/windows.yml)。Windows 进程退出断言使用持有的进程句柄，避免 PID 保留或复用导致误判。

## 尚未验证

- 第二台物理设备：完成的是同机独立目录搬迁和独立配置验证。
- WebView2 双实例三轮验证来自本地认证 HTTP 链路；当前任务经新插件实连覆盖的是原生桌面与 Chromium。
- 未修改上游的完整测试基线在 Windows 上仍有 7 个失败、18 个错误和 120 个跳过（260 项），涉及 POSIX 假设及过滤环境。上表的 277 项是统一程序专项回归，不代表上游全部测试通过。

详细原始日志、截图及运行配置保存在本机 verification 中，不公开，因为其中含设备和窗口信息。复现入口见[构建说明](docs/personal/构建说明.md)。
