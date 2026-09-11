import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type Locale = 'zh-CN' | 'en-US';

const STORAGE_KEY = 'ai-marking-locale';

/**
 * 国际化字典，方向为「中文 → 英文」。
 *
 * key 就是中文原文：`t('中文文案')` 在 `zh-CN` 下直接返回 key（不查表），
 * 只在 `en-US` 下查本表，未命中则回退 key。因此：
 * - 新增中文文案无需改这里，只有需要英文译文时才追加条目；
 * - 删除条目对中文界面零影响（英文模式退回显示中文）；
 * - 改文案必须同步改 key 与 `t()` 调用点，否则英文模式静默回退。
 *
 * 完整规则（引号、分节、验证路由）见 docs/frontend/i18n.md。
 */
const englishMessages: Record<string, string> = {
  'AI 作业批改': 'AI Marking',
  题目库: 'Question Library',
  历史记录: 'History',
  系统设置: 'Settings',
  跳转到主内容: 'Skip to main content',
  收起侧边栏: 'Collapse sidebar',
  展开侧边栏: 'Expand sidebar',
  'AI 作业批改系统': 'AI Marking System',
  English: 'English',
  中文: '中文',
  访问令牌: 'Access token',
  '访问令牌无效，请检查 backend/.env 中的 ACCESS_TOKEN':
    'Invalid token. Check ACCESS_TOKEN in backend/.env.',
  '请输入 backend/.env 中的 ACCESS_TOKEN': 'Enter ACCESS_TOKEN from backend/.env',
  '验证中…': 'Verifying…',
  进入: 'Enter',
  新版排队中: 'New version queued',
  新版识别中: 'Recognizing new version',
  可使用: 'Available',
  识别失败: 'Recognition failed',
  学生作业: 'Student assignment',
  '无效的记录 ID': 'Invalid record ID',
  '加载中...': 'Loading...',
  '加载结果中...': 'Loading result...',
  批改失败: 'Marking failed',
  批改过程中发生未知错误: 'An unknown error occurred during marking',
  '。你可以使用原文件重试，或让编程助手重新提交学生作业。':
    '. You can retry with the original file, or ask the programming assistant to resubmit the student assignment.',
  重新批改此记录: 'Mark this record again',
  批改结果: 'Marking result',
  '查看 AI 生成的评分、反馈与 OCR 原文': 'View AI-generated scores, feedback, and OCR text',
  已审阅: 'Reviewed',
  总分: 'Total score',
  'MCP 评分来源': 'MCP grading source',
  '置信度：': 'Confidence: ',
  '评分客户端：': 'Grading client: ',
  总体反馈: 'Overall feedback',
  无总体反馈: 'No overall feedback',
  详细评分项: 'Detailed rubric scores',
  无详细评分: 'No detailed scores',
  作业题目: 'Assignment question',
  '展开/折叠作业题目原文': 'Expand/collapse original question',
  'OCR 原文': 'OCR text',
  '展开/折叠 OCR 识别原文': 'Expand/collapse OCR text',
  '无 OCR 文本': 'No OCR text',
  查看评分工作台: 'Open marking workspace',
  返回历史: 'Back to history',
  批改流程: 'Marking workflow',
  '提交作业由编程助手完成，你只需确认成绩': 'Assignments are submitted by the programming assistant; you only confirm the final score',
  准备题目: 'Prepare a question',
  '先在题目库上传并识别题目 PDF，题目只需上传一次，之后可反复使用。':
    'Upload and recognize a question PDF in the question library once, then reuse it for any number of assignments.',
  编程助手提交作业: 'Programming assistant submits the assignment',
  '在编程助手（如 Codex）中打开本项目的 AI-Marking 工具，直接提供学生报告 PDF 与代码文件路径，由它调用本地 MCP 完成评分。':
    'Open the AI-Marking tool for this project in your programming assistant (e.g. Codex) and give it the report PDF and code file paths; it grades through the local MCP interface.',
  在网页确认成绩: 'Confirm the score here',
  '编程助手只保存评分建议；进入历史记录或评分工作台审阅，确认后提交最终成绩。':
    'The programming assistant only saves a grading suggestion; open History or the marking workspace to review and submit the final score.',
  前往题目库: 'Open question library',
  查看历史记录: 'View history',
  '学生作业通过编程助手（如 Codex）提交，本网页用于管理题目、查看评分并确认最终成绩。':
    'Student assignments are submitted through your programming assistant (e.g. Codex). This site manages questions, shows scores, and confirms final grades.',
  关闭: 'Close',
  '排队等待处理': 'Queued',
  'OCR 识别中': 'OCR in progress',
  'OCR 已完成': 'OCR complete',
  '等待 MCP 评分': 'Waiting for MCP grading',
  待审阅: 'Ready for review',
  失败: 'Failed',
  '编程助手指令已复制': 'Assistant instruction copied',
  '复制失败，请手动选择指令': 'Copy failed. Select the instruction manually.',
  '等待编程助手评分': 'Waiting for assistant grading',
  '已复制': 'Copied',
  复制编程助手指令: 'Copy assistant instruction',
  复制: 'Copy',
  '复制失败，请手动复制上方文本': 'Copy failed. Please copy the text above manually.',
  '复制提示词': 'Copy prompt',
  '批改提示词': 'Grading prompt',
  '复制后在编程助手（如 Codex）中粘贴，并在对话中上传学生作业 zip（一名学生一个 zip：报告 PDF + 代码文件）。':
    'Copy it, paste into your coding assistant (e.g. Codex), and upload the student zip in the chat (one zip per student: report PDF + code files).',
  '提示词生成失败': 'Failed to generate prompt',
  '该题目尚未完成 OCR 识别，无法生成批改提示词。请先在题目库确认识别完成后再试。':
    'This question has no OCR text yet, so the grading prompt cannot be generated. Confirm OCR is complete in the question library and try again.',
  '发起 AI 复核': 'Start AI review',
  'AI 复核': 'AI review',
  '重新发起 AI 复核': 'Start AI review again',
  '切换到英文': 'Switch to English',
  'Switch to Chinese': 'Switch to Chinese',
  页面出现异常: 'Something went wrong',
  '抱歉，应用遇到了未预期的问题。请尝试刷新页面，若问题持续存在请联系管理员。':
    'Sorry, the application encountered an unexpected problem. Refresh the page, and contact an administrator if it persists.',
  刷新页面: 'Refresh page',
  'AI 复核指令已复制，请粘贴给编程助手': 'AI review instruction copied. Paste it into your programming assistant.',
  'MCP 客户端': 'MCP client',
  来自: 'From',
  切换: 'Switch',
  '作业': 'Assignment',
  '这条作业的 OCR 已完成，正等待 MCP 客户端（如 Codex）评分。如果原任务已关闭，可复制下面的恢复指令继续处理，或使用待办列表工具发现作业。':
    'OCR is complete and the MCP client is expected to grade this assignment. If the original task was closed, copy the recovery instruction below or use the todo-list tool to find it.',
  '编程助手只会保存评分建议；最终成绩仍需教师回到此网页确认。':
    'The programming assistant only saves a grading suggestion. A teacher must return here to confirm the final score.',
  '题目提取': 'Question extraction',
  配置项: 'Configured',
  内置默认: 'Built-in default',
  复核同意: 'Review agrees',
  部分分歧: 'Partially disagrees',
  存在分歧: 'Disagrees',
  '尚无 AI 复核；可将作业交给另一个编程助手任务独立复核。':
    'No AI review yet. Send this assignment to another programming assistant task for an independent review.',
  '建议已更新（当前 revision': 'Suggestion updated (current revision',
  '复核针对 revision': 'reviewed revision',
  '），此结论已过期': '); this conclusion is stale',
  逐项复核结论: 'Item-by-item review conclusions',
  项分歧: 'disagreements',
  分歧: 'Disagree',
  同意: 'Agree',
  复核建议: 'Review suggestion',
  提交代码: 'Submitted code',
  '新作业由编程助手在当前任务中运行和核验；后端只保存源码与 SHA-256。':
    'New assignments are run and verified by the programming assistant; the backend only stores source code and its SHA-256 hash.',
  查看提交源代码: 'View submitted source code',
  源代码不可用: 'Source code unavailable',
  已重新进入批改队列: 'Added to the marking queue again',
  重新批改失败: 'Retry marking failed',
  本次批改失败: 'This marking attempt failed',
  '可以使用原文件重新批改；如果文件内容有问题，请让编程助手重新提交学生作业。':
    'You can retry with the original file. If the file is invalid, ask the programming assistant to resubmit the student assignment.',
  使用原文件重试: 'Retry with original file',
  评分已提交: 'Score submitted',
  '提交失败，请稍后重试': 'Submission failed. Try again later.',
  报告: 'Report',
  代码证据: 'Code evidence',
  调整作业与评分面板宽度: 'Adjust assignment and grading panel width',
  '调整评分面板与 AI 助手宽度': 'Resize grading panel and AI assistant',
  题目: 'Question',
  'PDF 加载失败': 'Failed to load PDF',
  '文件可能已过期或无法访问。请返回历史记录重新上传。':
    'The file may have expired or is inaccessible. Return to history and upload it again.',
  待处理: 'Pending',
  'OCR识别中': 'OCR in progress',
  'OCR完成': 'OCR complete',
  '等待MCP评分': 'Waiting for MCP grading',
  'OCR 失败：': 'OCR failed: ',
  '。请重新选择 PDF 上传。': '. Choose a PDF to upload again.',
  '新版识别失败：': 'New version recognition failed: ',
  '。旧版题目仍可继续使用。': '. The previous version remains available.',
  '尚未使用': 'Not used yet',
  最近使用: 'Last used',
  '份批改记录': 'marking records',
  '题目名称已更新': 'Question name updated',
  '输入新的题目名称': 'Enter a new question name',
  '新版已进入后台识别，成功后将清理': 'The new version is being recognized in the background. On success it will clean up',
  '上传新版': 'Upload new version',
  '重新上传文件': 'Upload file again',
  重命名: 'Rename',
  '等待识别': 'Waiting for recognition',
  正在识别: 'Recognizing',
  '全选当前页': 'Select all on this page',
  '删除中...': 'Deleting...',
  '题目已删除，清理了': 'Question deleted. Cleaned up',
  '条旧批改记录': 'old marking records',
  '题目只需上传并识别一次，之后可直接用于多份学生作业。':
    'Upload and recognize a question once, then reuse it for multiple student assignments.',
  '搜索题目名称或文件名': 'Search question or file name',
  '新版识别失败': 'New version recognition failed',
  '还没有可显示的题目': 'No questions to display',
  '上传第一份 PDF 题目开始使用': 'Upload your first question PDF to get started',
  上传题目: 'Upload question',
  '批量上传题目 PDF，上传后自动进行 OCR 识别，识别完成后即可在题目库中复用。':
    'Upload question PDFs in batch. Each is OCR-recognized automatically and becomes reusable in the question library once ready.',
  '上传题目文件': 'Upload question files',
  '仅支持 PDF 格式，单个文件不超过 50MB，可一次选择多个文件。':
    'PDF only. Each file must be 50MB or smaller; you can select multiple files at once.',
  '点击选择或拖拽 PDF 文件到此处': 'Click to choose or drag PDF files here',
  '拖拽 PDF 到此处，或点击选择文件': 'Drag PDFs here, or click to choose files',
  '支持一次选择多个文件，单个不超过 50MB；也可直接粘贴文件':
    'Choose multiple files at once, each up to 50MB; you can also paste files.',
  '「': '“',
  '」不是 PDF 文件，已跳过': '” is not a PDF and was skipped',
  '」超过 50MB，已跳过': '” exceeds 50MB and was skipped',
  '」已在列表中': '” is already in the list',
  待上传: 'Ready',
  上传中: 'Uploading',
  识别中: 'Recognizing',
  识别完成: 'Recognized',
  '「识别完成': '” recognized',
  '」识别完成': '',
  重试: 'Retry',
  清空列表: 'Clear list',
  移除: 'Remove',
  上传: 'Upload',
  '」已上传，正在识别': '” uploaded, recognizing',
  '」已上传，等待 OCR 识别': '” uploaded, waiting for OCR',
  '「OCR 识别失败': '',
  '」OCR 识别失败': '” failed OCR recognition',
  'OCR 识别失败': 'OCR recognition failed',
  '题目名称不能为空': 'Question name cannot be empty',
  题目名称: 'Question name',
  前往系统设置: 'Open Settings',
  '正在等待 OCR 识别，全部识别完成后将进入上传作业页…':
    'Waiting for OCR recognition. The assignment upload page opens once every file is recognized…',
  '上传后会自动等待 OCR 识别，完成后进入上传作业页。':
    'After upload, the system waits for OCR recognition and then opens the assignment upload page.',
  '识别完成，已为你预选，可直接上传作业答案':
    ' finished recognizing and is preselected. You can upload the assignment now.',
  '批量上传题目 PDF，系统会等待 OCR 识别完成后引导你上传作业答案。':
    'Upload question PDFs in bulk. The system waits for OCR recognition, then guides you to upload assignment answers.',
  '上传成功后会自动进入识别队列。': 'Uploaded questions automatically join the recognition queue.',
  取消: 'Cancel',
  删除题目: 'Delete question',
  使用新版替换题目: 'Replace question with a new version',
  '此操作会永久删除该题目关联的': 'This permanently deletes the question and its',
  '条批改记录和学生 PDF，无法恢复。请输入题目名称确认：': ' marking records and student PDFs. This cannot be undone. Enter the question name to confirm:',
  '我已知晓：替换成功后将永久删除上述': 'I understand that a successful replacement permanently deletes the above',
  '条历史批改记录及其学生 PDF，不可恢复。': ' historical marking records and their student PDFs.',
  输入完整题目名称: 'Enter the complete question name',
  确认并永久: 'Permanently ',
  替换: 'replace',
  'MCP 评分自检': 'MCP grading self-check',
  '配置 OCR 解析、评分标准与 MCP 自检开关': 'Configure OCR parsing, grading rubrics, and MCP checks',
  '编程助手(Codex 等)通过本地 MCP 接口完成评分与复核': 'Programming assistants (such as Codex) grade and review through the local MCP interface',
  '要求客户端在保存建议前完成第二遍反向自检': 'Require the client to perform a second reverse check before saving a suggestion',
  '评分流程要求 MCP 客户端对每条评分项做反向校验（依据原文引用与分数上限推导），双重检查通过后才能保存建议，最终成绩仍需教师在此网页确认。': 'The MCP client must reverse-check each rubric item using source evidence and score limits. Suggestions are saved only after both checks pass, and a teacher must confirm the final score here.',
  '将 PDF 解析为结构化 Markdown，保留表格、公式与阅读顺序': 'Parse PDFs into structured Markdown while preserving tables, formulas, and reading order',
  '请填写完整接口：异步任务入口 .../api/v2/ocr/jobs，或同步入口 .../layout-parsing；不要填写 .../api/v2/ocr/jobs/layout-parsing。': 'Use the complete endpoint: async .../api/v2/ocr/jobs or sync .../layout-parsing. Do not use .../api/v2/ocr/jobs/layout-parsing.',
  '请输入 PaddleOCR Access Token': 'Enter the PaddleOCR access token',
  'AI Studio 个人访问令牌': 'AI Studio personal access token',
  '评分标准(Rubric)': 'Grading rubric',
  '题目提取的评分标准优先；没有可信提取结果时使用当前配置': 'A verified rubric extracted from the question takes priority; the current profile is used when none is available',
  '重置为默认': 'Reset to default',
  '结构化 Rubric JSON': 'Structured rubric JSON',
  '保存配置': 'Save configuration',
  '共': 'Total',
  '条记录': 'records',
  已选: 'Selected',
  项: 'items',
  '查看已上传作业的批改状态与评分结果': 'View marking status and scores for uploaded assignments',
  批改记录: 'Marking records',
  文件名: 'File name',
  状态: 'Status',
  分数: 'Score',
  上传时间: 'Uploaded',
  操作: 'Actions',
  '暂无批改记录': 'No marking records',
  选择: 'Select',
  导出分析: 'Export analysis',
  导出成绩分析: 'Export grade analysis',
  '将导出该题目下所有已审阅的最终成绩。':
    'This exports all teacher-reviewed final results for the question.',
  '本次及格线（得分率 %）': 'Pass threshold (score rate %)',
  '分数段固定按得分率划分；此数值只决定及格率和是否达标。':
    'Score bands are fixed by score rate; this value only controls pass rate and pass status.',
  '请输入大于 0 且不超过 100 的数值': 'Enter a number greater than 0 and no more than 100',
  '生成并下载 Excel': 'Generate and download Excel',
  '正在生成...': 'Generating...',
  'Excel 成绩分析已生成': 'Excel grade analysis generated',
  '该题目暂无批改记录': 'This question has no marking records',
  批改完成后方可删除: 'Can be deleted after marking completes',
  查看: 'View',
  上一页: 'Previous',
  第: 'Page',
  页: 'page',
  下一页: 'Next',
  确认删除: 'Confirm deletion',
  即将删除: 'About to delete',
  '条批改记录，此操作不可撤销，关联的 PDF 文件将一并清除。': 'marking records. This cannot be undone, and associated PDF files will also be removed.',
  已删除: 'Deleted',
  '上传时间：': 'Uploaded: ',
  '完成时间：': 'Completed: ',
  完成于: 'Completed ',
  '作业题目：': 'Question: ',
  '审核教师：': 'Reviewer: ',
  '证据：': 'Evidence: ',
  '还没有评分建议': 'No grading suggestion yet',
  编程助手建议评分: 'Programming assistant suggestion',
  置信度: 'Confidence',
  最终成绩以教师确认提交为准: 'The final score is determined when a teacher confirms it',
  总评反馈: 'Overall feedback',
  '输入最终反馈…': 'Enter final feedback…',
  单项得分不能超过该项满分: 'An item score cannot exceed its maximum',
  '该项评分说明…': 'Explain this score…',
  原文证据: 'Source evidence',
  各评分项得分之和: 'The sum of item scores',
  超过总分: 'exceeds the total score',
  '暂不可提交。': 'Submission is unavailable.',
  各评分项满分之和: 'The sum of item maximums',
  与总满分: 'does not match the total maximum',
  '不一致，暂不可提交。': ' and cannot be submitted.',
  '该作业已审阅，成绩已锁定': 'This assignment has been reviewed and the score is locked',
  复核并提交最终评分: 'Review and submit final score',
  配置项目: 'Configuration profile',
  删除: 'Delete',
  'PaddleOCR-VL 文档解析': 'PaddleOCR-VL document parsing',
  新建配置项目: 'New configuration profile',
  确定: 'OK',
  设为默认: 'Set as default',
  '文件已重新上传，正在识别': 'File re-uploaded, recognizing',
  '条批改记录': 'marking records',
  'Rubric 必须是合法 JSON': 'Rubric must be valid JSON',
  '配置已保存': 'Configuration saved',
  'Rubric 已清空，保存后评分标准将由客户端从题目中提取':
    'Rubric cleared. After saving, the rubric will be extracted from the question by the client.',
  '请输入配置项目名称': 'Enter a configuration profile name',
  '配置项目已创建': 'Configuration profile created',
  '配置项目已重命名': 'Configuration profile renamed',
  '已复制为新配置项目': 'Copied as a new configuration profile',
  '配置项目已删除': 'Configuration profile deleted',
  '已设为默认配置项目': 'Set as the default configuration profile',
  '加载配置中...': 'Loading configuration...',
  '加载配置失败': 'Failed to load configuration',
  '上传题目使用的配置项目': 'Configuration profile used for question upload',
  // ACP 批改助手目录(设置页)
  批改助手目录: 'Marking assistant catalog',
  '从 ACP Registry 发现的审核白名单助手；安装后可发起自动批改':
    'Whitelisted assistants discovered from the ACP Registry. Install one to start automatic marking.',
  刷新: 'Refresh',
  'Registry 已刷新': 'Registry refreshed',
  '加载助手目录中...': 'Loading assistant catalog...',
  'Registry 暂不可用，且没有本地缓存目录':
    'Registry is unavailable and there is no cached catalog.',
  已连接: 'Connected',
  待登录: 'Login required',
  连接失败: 'Connection failed',
  不支持: 'Unsupported',
  'Codex ACP 安装与连接测试；连接后可发起自动批改':
    'Install and test Codex ACP; start automatic marking after the connection is ready.',
  'Codex 原生工作区沙箱，网络已关闭':
    'Codex native workspace sandbox; network access is disabled.',
  已装: 'installed',
  未安装: 'Not installed',
  可更新到: 'update available:',
  测试连接: 'Test connection',
  更新: 'Update',
  安装: 'Install',
  连接测试通过: 'Connection test passed',
  助手需要先在本机登录: 'The assistant needs to log in on this machine first',
  连接测试失败: 'Connection test failed',
  已安装版本: 'Installed version',
  版本是最新: 'Already up to date:',
  已设为默认批改助手: 'Set as the default marking assistant',
  'Registry 版本': 'Registry version',
  '登录凭证由各助手自行管理；连接测试不会保存任何密钥。':
    'Login credentials are managed by each assistant. Connection tests never store any secrets.',
  // ACP 自动批改(审阅页)
  自动批改: 'Automatic marking',
  '启动 ACP 自动批改': 'Start ACP automatic marking',
  '选择一个已安装的批改助手,由它在隔离工作区完成评分并保存建议。':
    'Pick an installed marking assistant. It grades the work in an isolated workspace and saves a suggestion.',
  '尚无已安装的助手,请先到「系统设置 → 批改助手目录」安装。':
    'No installed assistants yet. Install one in Settings → Marking assistant catalog first.',
  开始批改: 'Start marking',
  '启动 Codex 自动批改': 'Start Codex automatic marking',
  '点击后会在右侧 AI 助手中填入批改指令;你先调整模型、思考强度与权限档位,再手动发送开始批改。':
    'The grading instruction will be placed in the AI assistant on the right. Adjust the model, reasoning effort and permission mode first, then send it to start marking.',
  '批改运行进行中,对话已暂时冻结。':
    'A marking run is in progress — chat is temporarily frozen.',
  '窗口宽度不足,请拉宽窗口后再打开 AI 助手':
    'Window is too narrow — widen it to open the AI assistant',
  运行配置: 'Run configuration',
  配置已更新: 'Configuration updated',
  '配置已排队,将在下一回合生效':
    'Configuration queued — takes effect on the next turn.',
  '新配置将在下一回合生效':
    'The new configuration takes effect on the next turn.',
  'ACP 批改运行': 'ACP marking run',
  排队中: 'Queued',
  启动助手中: 'Starting assistant',
  批改进行中: 'Marking in progress',
  等待教师确认: 'Waiting for teacher confirmation',
  已完成: 'Completed',
  已取消: 'Cancelled',
  尝试次数: 'Attempts',
  取消批改: 'Cancel marking',
  已请求取消: 'Cancellation requested',
  教师检查点: 'Teacher checkpoint',
  '备注(可选)': 'Note (optional)',
  '确认一致,继续': 'Consistent — continue',
  '不一致,要求修正': 'Mismatch — request correction',
  '答复已保存,批改将继续': 'Reply saved. Marking will continue.',
  执行转录: 'Execution transcript',
  '评分建议已保存,页面将自动进入复核。':
    'Grading suggestion saved. The page will switch to review automatically.',
  '批改运行失败,可重新发起。': 'The marking run failed. You can start a new one.',
  '批改运行已取消,可重新发起。':
    'The marking run was cancelled. You can start a new one.',
  // ACP 对话面板(审阅页右侧)
  'AI 助手': 'AI assistant',
  'ACP 助手': 'ACP assistant',
  新对话: 'New chat',
  收起对话面板: 'Collapse chat panel',
  对话: 'Chat',
  '想聊点关于这份作业的什么?': 'What would you like to ask about this submission?',
  '批改助手会读取本作业的报告与代码,逐条回答你的追问。':
    'The assistant reads this submission report and code, then answers your follow-ups one by one.',
  随心输入: 'Type anything…',
  助手: 'Assistant',
  模型: 'Model',
  默认模型: 'Default model',
  当前默认: 'current default',
  会话历史: 'Session history',
  对话会话已创建: 'Chat session created',
  空闲: 'Idle',
  回复中: 'Replying',
  等待批准: 'Waiting for approval',
  已关闭: 'Closed',
  异常: 'Error',
  停止: 'Stop',
  关闭会话: 'Close session',
  已关闭会话: 'Session closed',
  '批改流程进行中,暂不可继续对话。':
    'Marking in progress. Chat is unavailable right now.',
  '输入消息,Enter 发送,Shift+Enter 换行':
    'Type a message. Enter to send, Shift+Enter for a new line',
  消息输入框: 'Message input',
  发送: 'Send',
  发送中: 'Sending',
  助手请求批准: 'Assistant requests approval',
  未知的工具操作: 'Unknown tool action',
  批准: 'Approve',
  拒绝: 'Deny',
  // 终端卡与代码块
  运行中: 'Running',
  复制命令: 'Copy command',
  复制输出: 'Copy output',
  复制代码: 'Copy code',
  // ACP 新对话底部工具栏(Zed 风格)
  权限档位: 'Permission mode',
  请求批准: 'Ask for approval',
  自动批准: 'Approve for me',
  模型与思考强度: 'Model & thinking effort',
  'Ask for approval': 'Ask for approval',
  'Approve for me': 'Approve for me',
  'Codex 模型': 'Codex model',
  'Agent 默认模型': 'Agent default model',
  思考强度: 'Thinking effort',
  'Agent 默认思考': 'Agent default thinking',
  'Agent 默认': 'Agent default',
  快速: 'Fast',
  标准: 'Standard',
  'Fast mode': 'Fast mode',
  确认快速模式: 'Confirm Fast mode',
  '快速模式可能提高 ChatGPT 额度或 API 成本。':
    'Fast mode may increase ChatGPT quota or API cost.',
  '当前账户、模型或 Agent 版本未声明快速模式，暂仅支持标准模式。':
    'The current account, model, or agent version does not declare Fast mode; only Standard mode is available.',
  '读取 Codex 配置能力中…': 'Reading Codex configuration capabilities…',
  '请先在系统设置安装并连接测试 Codex ACP。':
    'Install and connect Codex ACP in Settings first.',
  'Codex ACP': 'Codex ACP',
  // ACP 对话省略号菜单与永久删除
  更多操作: 'More actions',
  删除对话: 'Delete conversation',
  对话已永久删除: 'Conversation permanently deleted',
  删除对话失败: 'Failed to delete conversation',
  永久删除: 'Delete permanently',
  '将永久删除该对话的聊天记录、事件转录与专属工作区，此操作不可恢复。':
    'This permanently deletes the conversation history, event transcript, and its dedicated workspace. This cannot be undone.',
  // 网页端上传作业
  上传作业: 'Upload assignment',
  '在网页端上传学生作业：选择题目、报告 PDF 与代码文件，提交后选择评分方式，系统会自动进入批改流程。':
    'Upload a student assignment here: pick a question, the report PDF and code files, choose a grading mode, and the system enters the marking flow automatically.',
  作业信息: 'Assignment info',
  '选择作业对应的题目，并上传学生报告 PDF。':
    'Choose the question this assignment belongs to and upload the student assignment file.',
  选择题目: 'Question',
  请选择题目: 'Select a question',
  '暂无题目，请先上传题目': 'No questions yet. Upload a question first',
  前往上传题目: 'Go to question upload',
  '学生作业文件': 'Assignment file',
  '选择作业文件': 'Choose the assignment file',
  '点击选择作业文件': 'Choose the assignment file',
  '选择报告 PDF 或作业 ZIP': 'Pick a report PDF or an assignment ZIP',
  '支持纯 PDF(≤50MB),或 ZIP 包(≤150MB,内含报告 PDF、代码文件与数据集)':
    'A single PDF (≤50MB) or a ZIP archive (≤150MB) containing the report PDF, code files and datasets',
  '文件仅支持 PDF 或 ZIP 格式': 'Only PDF or ZIP files are supported',
  'ZIP 超过 150MB，请压缩后重试': 'The ZIP exceeds 150MB. Compress it and retry',
  'ZIP 内容预览': 'ZIP contents preview',
  '上传时 ZIP 由系统解包自动分类：报告 PDF、代码文件(文件名需以小题号结尾,如 task3.py)与其余文件将作为数据集随批改提供。':
    'The ZIP is unpacked and classified automatically on upload: the report PDF, code files (filenames must end with the sub-question number, e.g. task3.py) and remaining files as datasets are provided to grading.',
  '正在解析 ZIP 内容...': 'Parsing ZIP contents…',
  '报告 PDF': 'Report PDF',
  '代码文件': 'Code files',
  '数据集': 'Datasets',
  '未找到': 'Not found',
  '无代码文件': 'No code files',
  '无数据集': 'No datasets',
  '已忽略': 'Ignored',
  '个系统文件(如 .DS_Store)': 'system file(s) such as .DS_Store',
  '数据集文件': 'Dataset files',
  '批改时随代码一并物化到隔离工作区,供程序运行读取。':
    'Materialized into the isolated workspace together with the code for programs to read during grading.',
  '学生报告 PDF': 'Student report PDF',
  '选择报告 PDF': 'Choose the report PDF',
  '点击选择报告 PDF': 'Choose the report PDF',
  '仅支持 PDF 格式，单个文件不超过 50MB':
    'PDF only, up to 50MB per file',
  '代码文件（可选）': 'Code files (optional)',
  '支持多份代码文件，为每份指定所属小题；同一小题有多个文件时需标记一个入口文件。':
    'Upload multiple code files and map each to a sub-question. When one sub-question has several files, mark exactly one as the entry file.',
  选择代码文件: 'Choose code files',
  '选择代码文件输入框': 'Code files input',
  小题号: 'Sub-question',
  入口文件: 'Entry file',
  清空: 'Clear',
  评分方式: 'Grading mode',
  '选择本次作业的评分方式，上传后自动进入对应批改流程。':
    'Choose how this assignment is graded. The system enters the matching flow after upload.',
  '使用 ACP 自动批改': 'Grade with ACP',
  '由本机已安装的批改助手在隔离工作区自动评分，上传后自动启动。':
    'An installed assistant grades it automatically in an isolated workspace right after upload.',
  '默认以 Ask for approval 档位启动，批改会话中可随时调整模型与权限。':
    'Starts in the Ask for approval mode; you can adjust the model and permission mode anytime in the marking session.',
  '使用 MCP 等待外部编程助手': 'Wait for external assistant (MCP)',
  '上传后由外部编程助手调用本地 MCP 评分，评分建议进入待审阅列表。':
    'An external programming assistant grades it via the local MCP after upload; the suggestion lands in the review list.',
  批改助手: 'Assistant',
  '上传成功后跳转到批改详情页，等待外部编程助手（如 Codex）调用本地 MCP 评分。':
    'After upload you are taken to the review page to wait for the external assistant (e.g. Codex) to grade via the local MCP.',
  开始上传: 'Upload',
  '作业已上传，正在进入批改流程': 'Assignment uploaded. Entering the marking flow',
  请先选择题目: 'Select a question first',
  '请先选择报告 PDF': 'Choose the report PDF first',
  请先选择批改助手: 'Choose an assistant first',
  '请选择 PDF 文件': 'Please choose a PDF file',
  '文件超过 50MB，请压缩后重试': 'The file exceeds 50MB. Compress it and retry',
  每道小题需各选一个入口文件: 'Each sub-question needs exactly one entry file',
  // 复核面板输入校验
  '单项得分不能为负数': 'Item score cannot be negative',
  '以下评分项得分未填写或非法：': 'These items have missing or invalid scores: ',
  '的小题号必须是正整数': ' must have a positive integer sub-question number',
  '」的小题号必须是正整数': '” must have a positive integer sub-question number',
  '」识别完成，已为你预选，可直接上传作业答案':
    '” finished recognizing and is preselected. You can upload the assignment now.',
  '沙箱与网络限制由 Codex 原生配置决定，应用不再叠加限制。':
    'Sandbox and network restrictions follow the Codex native configuration; this app adds no extra limits.',
  // ACP 助手目录(设置页)
  卸载: 'Uninstall',
  卸载已安装链接: 'Uninstall installed link',
  尚未安装: 'Not installed',
  版本已是最新: 'Version is up to date',
  已卸载安装链接: 'Install link removed',
  '安装（Registry 无版本信息）': 'Install (no version from registry)',
  '确认卸载该助手？已安装链接将被删除，助手仍保留在目录中。':
    'Uninstall this assistant? The installed link is removed; the assistant stays in the directory.',
  // 批改运行 / 对话面板 / 记录页错误分支
  'Codex ACP 批改运行': 'Codex ACP marking run',
  会话加载失败: 'Failed to load the chat session',
  加载记录失败: 'Failed to load the record',
  加载详情失败: 'Failed to load the details',
};

function detectLocale(): Locale {
  if (typeof window === 'undefined') return 'zh-CN';
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'zh-CN' || saved === 'en-US') return saved;
  } catch {
    // Ignore unavailable storage and use the browser locale.
  }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}

interface LanguageContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: (key: string, fallback?: string) => string;
}

const LanguageContext = createContext<LanguageContextValue>({
  locale: 'zh-CN',
  setLocale: () => undefined,
  toggleLocale: () => undefined,
  t: (key, fallback) => fallback ?? key,
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectLocale);

  const setLocale = useCallback((nextLocale: Locale) => {
    setLocaleState(nextLocale);
    try {
      window.localStorage.setItem(STORAGE_KEY, nextLocale);
    } catch {
      // The UI can still switch when storage is unavailable.
    }
  }, []);

  const toggleLocale = useCallback(() => {
    setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN');
  }, [locale, setLocale]);

  const t = useCallback(
    (key: string, fallback?: string) => {
      if (locale === 'zh-CN') return fallback ?? key;
      return englishMessages[key] ?? fallback ?? key;
    },
    [locale],
  );

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo(
    () => ({ locale, setLocale, toggleLocale, t }),
    [locale, setLocale, toggleLocale, t],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  return useContext(LanguageContext);
}

export { STORAGE_KEY };
