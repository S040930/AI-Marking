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
  '批改 agent 的主通道：使用 API key 直接调用 OpenAI Responses API':
    'The primary channel for the grading agent: call the OpenAI Responses API directly with your API key.',
  '网页上传作业': 'Upload in the web app',
  '上传学生报告 PDF 与代码文件（或包含全部文件的 ZIP），在详情页选择模型通道后开始批改，AI 批改 agent 自动完成评分。':
    'Upload the student report PDF and code files (or a ZIP containing everything), choose the model channel on the review page and start marking; the built-in AI grading agent scores it automatically.',
  '评分建议生成后进入批改详情页核对，必要时人工调整，确认后提交最终成绩。':
    'Once the suggestion is ready, open the review page to check it, adjust manually if needed, and confirm the final score.',
  '网页端管理题目、上传学生作业，由 AI 批改 agent 评分并确认最终成绩。':
    'Manage questions, upload student assignments, let the AI grading agent score them, and confirm final grades — all in the web app.',
  // 批改 agent(内嵌)与模型通道
  '开始 AI 批改': 'Start AI marking',
  '批改已启动': 'Marking started',
  '批改由本机后端直接调用模型完成，不再需要外部编程助手。':
    'Marking calls the model directly from the local backend — no external coding assistant needed.',
  '官方 OpenAI API': 'OpenAI API',
  'ChatGPT 订阅': 'ChatGPT Subscription',
  'ChatGPT 订阅通道': 'ChatGPT Subscription channel',
  '使用 ChatGPT 订阅额度调用模型；通过 ChatGPT OAuth 登录授权。':
    'Use your ChatGPT subscription quota. Sign in via ChatGPT OAuth.',
  '订阅通道依赖非公开接口，存在账号风控风险；如遇限流或接口变动，可随时切回官方 API 通道。':
    'The subscription channel relies on non-public endpoints and carries account-risk concerns. If rate limited or the endpoint changes, switch back to the official API channel.',
  '已登录': 'Signed in',
  '未登录': 'Not signed in',
  '账号': 'Account',
  '访问令牌有效至': 'Access token valid until',
  '到期自动刷新': 'refreshes automatically',
  '尚未登录。登录后可在评审页选择该通道，使用订阅额度批改。':
    'Not signed in. After signing in you can pick this channel on the review page and grade with your subscription quota.',
  '退出登录': 'Sign out',
  '登录 ChatGPT': 'Sign in to ChatGPT',
  '等待浏览器完成登录…': 'Waiting for the browser to finish signing in…',
  '没有自动打开？': 'Did not open automatically?',
  '点击打开授权页': 'Open the authorization page',
  '取消登录': 'Cancel sign-in',
  'ChatGPT 订阅登录成功': 'Signed in to ChatGPT Subscription',
  '登录失败，请重试': 'Sign-in failed. Please try again.',
  '已打开浏览器，请完成 ChatGPT 登录':
    'Browser opened — finish signing in to ChatGPT.',
  '已退出 ChatGPT 订阅': 'Signed out of ChatGPT Subscription',
  '请先在设置页登录 ChatGPT 订阅。':
    'Sign in to ChatGPT Subscription on the Settings page first.',
  '已配置': 'Configured',
  '未配置': 'Not configured',
  '当前已配置': 'Currently configured',
  '尚未配置': 'Not configured yet',
  '留空表示不修改': 'leave blank to keep unchanged',
  '请输入 OpenAI API key': 'Enter your OpenAI API key',
  'API 地址(可选)': 'API base URL (optional)',
  '默认思考强度': 'Default thinking effort',
  '模型默认': 'Model default',
  '模型 ID': 'Model ID',
  '模型 ID(留空用默认)': 'Model ID (blank = default)',
  '正在加载模型列表...': 'Loading models...',
  '模型列表暂不可用，将使用默认模型。': 'Model list is unavailable; the default model will be used.',
  '应用': 'Apply',
  '保存通道配置': 'Save channel config',
  '连接测试': 'Test connection',
  '连接正常': 'Connection OK',
  '连接测试未通过': 'Connection test failed',
  '个可用模型': 'models available',
  '通道配置已保存': 'Channel config saved',
  '默认通道已更新': 'Default channel updated',
  '加载模型通道中...': 'Loading model channels...',
  '加载通道配置中...': 'Loading channel config...',
  '配置没有变化': 'No changes to save',
  '默认批改通道': 'Default marking channel',
  '发起批改时未指定通道则使用默认通道': 'Used when a marking run does not specify a channel',
  '请先在设置页配置官方 OpenAI API key。':
    'Configure your OpenAI API key in Settings first.',
  '前往设置': 'Go to Settings',
  'AI 批改运行': 'AI marking run',
  '审核自动测试集': 'Review automatic tests',
  '个测试项': 'test cases',
  '测试集已批准': 'Test suite approved',
  '正在重新生成测试集': 'Regenerating test suite',
  '测试集仅根据题目 OCR 与可信 rubric 生成。批准后同题作业复用此版本；测试结果会提供给 AI 作为评分证据。':
    'The suite uses only question OCR and the trusted rubric. Once approved, this version is reused for the question; test results become evidence for AI scoring.',
  '批准并运行测试': 'Approve and run tests',
  '重新生成': 'Regenerate',
  '标准输入/输出': 'stdin / stdout',
  'Notebook 断言': 'Notebook assertion',
  '输入': 'Input',
  '预期输出': 'Expected output',
  '自动测试结果': 'Automatic test results',
  '项通过': 'passed',
  '通过': 'Passed',
  '未通过': 'Failed',
  '编译错误': 'Compile error',
  '运行异常': 'Runtime error',
  '超时': 'Timed out',
  'Notebook kernel 不匹配': 'Notebook kernel mismatch',
  '缺少代码': 'Code missing',
  '容器隔离（完整）': 'Container isolation (full)',
  '沙箱隔离（受限）': 'Sandbox isolation (limited)',
  '无隔离': 'No isolation',
  '重新生成此题的自动测试集': 'Regenerate automatic tests for this question',
  '默认复用已批准版本；勾选后会生成新版本并在运行前请你审核。':
    'The approved version is reused by default. Check this to generate a new version for your review before tests run.',
  'AI 批改': 'AI marking',
  '外部助手(历史)': 'External assistant (legacy)',
  '评分来源': 'Grading source',
  '评分通道：': 'Grading channel: ',
  'Token 用量': 'Token usage',
  '检查点备注': 'Checkpoint note',
  '可在本地运行学生代码后核对结果再确认，或直接确认；选择"不一致"时必须填写说明。':
    'Optionally run the student code locally to verify the result before confirming, or confirm directly. Choosing "mismatch" requires a note.',
  '配置模型通道、OCR 解析与评分标准': 'Configure model channels, OCR parsing and grading rubric',
  '上传后自动进入批改流程，由 AI 批改 agent 完成评分。':
    'After upload the assignment enters the marking flow; the built-in AI grading agent produces the score.',
  '上传成功后跳转到批改详情页：在详情页选择模型通道与思考强度，点击「开始批改」即可；评分建议生成后由教师确认最终成绩。':
    'After upload you are taken to the review page: choose the model channel and thinking effort, then click "Start marking". The teacher confirms the final score once the suggestion is ready.',
  '在网页端上传学生作业：选择题目、报告 PDF 与代码文件，提交后进入批改流程。':
    'Upload a student assignment: pick a question, the report PDF and code files; the marking flow starts after upload.',
  '评分建议已更新，已保留你的修改。请载入最新建议后重新复核。': 'The suggestion has changed. Your edits have been kept. Load the latest suggestion and review it again.',
  '载入最新建议': 'Load latest suggestion',
  '评分状态已变化，请核对最新结果后重新复核': 'The grading state has changed. Check the latest result before reviewing again.',

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
  准备题目: 'Prepare a question',
  '先在题目库上传并识别题目 PDF，题目只需上传一次，之后可反复使用。':
    'Upload and recognize a question PDF in the question library once, then reuse it for any number of assignments.',
  在网页确认成绩: 'Confirm the score here',
  前往题目库: 'Open question library',
  查看历史记录: 'View history',
  关闭: 'Close',
  '排队等待处理': 'Queued',
  'OCR 识别中': 'OCR in progress',
  'OCR 已完成': 'OCR complete',
  待审阅: 'Ready for review',
  失败: 'Failed',
  '复制失败，请手动选择指令': 'Copy failed. Select the instruction manually.',
  '已复制': 'Copied',
  复制: 'Copy',
  '复制失败，请手动复制上方文本': 'Copy failed. Please copy the text above manually.',
  '批改提示词': 'Grading prompt',
  'AI 复核': 'AI review',
  '切换到英文': 'Switch to English',
  'Switch to Chinese': 'Switch to Chinese',
  页面出现异常: 'Something went wrong',
  '抱歉，应用遇到了未预期的问题。请尝试刷新页面，若问题持续存在请联系管理员。':
    'Sorry, the application encountered an unexpected problem. Refresh the page, and contact an administrator if it persists.',
  刷新页面: 'Refresh page',
  来自: 'From',
  切换: 'Switch',
  '作业': 'Assignment',
  '题目提取': 'Question extraction',
  配置项: 'Configured',
  内置默认: 'Built-in default',
  复核同意: 'Review agrees',
  部分分歧: 'Partially disagrees',
  存在分歧: 'Disagrees',
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
  题目: 'Question',
  'PDF 加载失败': 'Failed to load PDF',
  '文件可能已过期或无法访问。请返回历史记录重新上传。':
    'The file may have expired or is inaccessible. Return to history and upload it again.',
  待处理: 'Pending',
  'OCR识别中': 'OCR in progress',
  'OCR完成': 'OCR complete',
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
  刷新: 'Refresh',
  'Registry 已刷新': 'Registry refreshed',
  '加载助手目录中...': 'Loading assistant catalog...',
  'Registry 暂不可用，且没有本地缓存目录':
    'Registry is unavailable and there is no cached catalog.',
  已连接: 'Connected',
  待登录: 'Login required',
  连接失败: 'Connection failed',
  不支持: 'Unsupported',
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
  自动批改: 'Automatic marking',
  开始批改: 'Start marking',
  运行配置: 'Run configuration',
  配置已更新: 'Configuration updated',
  '配置已排队,将在下一回合生效':
    'Configuration queued — takes effect on the next turn.',
  '新配置将在下一回合生效':
    'The new configuration takes effect on the next turn.',
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
  '仅显示最近 300 条事件': 'Showing only the latest 300 events',
  主管: 'Supervisor',
  评分项: 'Rubric item',
  '评分建议已保存,页面将自动进入复核。':
    'Grading suggestion saved. The page will switch to review automatically.',
  '批改运行失败,可重新发起。': 'The marking run failed. You can start a new one.',
  '批改运行已取消,可重新发起。':
    'The marking run was cancelled. You can start a new one.',
  'AI 助手': 'AI assistant',
  新对话: 'New chat',
  收起对话面板: 'Collapse chat panel',
  对话: 'Chat',
  '想聊点关于这份作业的什么?': 'What would you like to ask about this submission?',
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
  权限档位: 'Permission mode',
  请求批准: 'Ask for approval',
  自动批准: 'Approve for me',
  模型与思考强度: 'Model & thinking effort',
  'Ask for approval': 'Ask for approval',
  'Approve for me': 'Approve for me',
  思考强度: 'Thinking effort',
  快速: 'Fast',
  标准: 'Standard',
  'Fast mode': 'Fast mode',
  确认快速模式: 'Confirm Fast mode',
  更多操作: 'More actions',
  删除对话: 'Delete conversation',
  对话已永久删除: 'Conversation permanently deleted',
  删除对话失败: 'Failed to delete conversation',
  永久删除: 'Delete permanently',
  // 网页端上传作业
  上传作业: 'Upload assignment',
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
  '默认以 Ask for approval 档位启动，批改会话中可随时调整模型与权限。':
    'Starts in the Ask for approval mode; you can adjust the model and permission mode anytime in the marking session.',
  批改助手: 'Assistant',
  开始上传: 'Upload',
  '作业已上传，正在进入批改流程': 'Assignment uploaded. Entering the marking flow',
  请先选择题目: 'Select a question first',
  '请先选择报告 PDF': 'Choose the report PDF first',
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
  卸载: 'Uninstall',
  卸载已安装链接: 'Uninstall installed link',
  尚未安装: 'Not installed',
  版本已是最新: 'Version is up to date',
  已卸载安装链接: 'Install link removed',
  '安装（Registry 无版本信息）': 'Install (no version from registry)',
  // 批改运行 / 对话面板 / 记录页错误分支
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
