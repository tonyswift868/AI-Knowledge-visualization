"""Presentation resources. Add a locale catalog without changing Qt business code."""
CATALOGS = {
    'zh-CN': {
        'labels': {
            'overview': '项目概览', 'devices': '跨设备测试',
            'title': 'MentorFlow 开发工作台', 'title_compact': 'MentorFlow\n开发工作台', 'phase': '当前阶段：{value}',
            'overall': '整体状态：{value}', 'integration': '当前集成版本：{sha}',
            'snapshot': '根据最近已登记的项目状态展示', 'action': '现在需要你做什么',
            'target': '当前测试版本', 'workflow': '开发流程', 'workers': '员工状态',
            'version': '版本 {version}', 'purpose': '测试目的：{purpose}',
            'blocked': '当前阻塞：{stages}', 'engineering': '工程详情',
            'worker_blocker': '员工 {slot}',
            'worker_details': '员工 {slot} · 工程详情', 'version_details': '测试版本详情',
            'actions_details': '操作说明', 'history': '测试历史（只读）',
            'account': '测试账号：{username}\n密码：SECURE_REFERENCE_ONLY\n引用：{reference}',
        },
        'statuses': {
            'RUNNING': '进行中', 'SILENT': '待命', 'BLOCKED': '已阻塞',
            'USER_ACTION_REQUIRED': '等待你操作', 'PASS_STAGE': '本阶段已完成',
            'WAITING_FOR_ARTIFACT': '等待测试版本', 'NOT_RUN': '尚未执行',
            'LOCKED': '等待前置条件', 'UNKNOWN': '状态未知', 'PASS': '已完成',
            'NOT_CREATED': '未创建', 'READY': '可以测试', 'WAITING': '等待测试版本生成',
            'NONE': '当前没有需要测试的版本', 'NORMAL': '正常推进',
            'FAIL': '未通过', 'SUPERSEDED': '已被新版本替代', 'CURRENT': '当前测试版',
        },
        'actions': {
            'copy': '复制', 'refresh': '刷新状态', 'engineering': '工程详情',
            'launch': '启动当前测试版', 'action_launch': '启动测试版本',
            'instructions': '查看操作说明', 'directory': '打开所在目录',
            'version_details': '查看版本详情', 'receipt': '打开回执', 'log': '打开日志',
            'copy_source': '复制版本标识', 'handoff': '打开网页版接手页',
            'close': '收起详情', 'worker': '查看员工 {slot} 详情',
        },
        'workflow': {
            'login': '真实登录', 'continuity': '旧数据连续性',
            'account': '注册 / 邮箱 / 找回密码', 'update': '最终安装与更新',
            'formal': '正式 main3', 'not_started': '未开始',
            'smtp_pass': '邮件传输已完成；投递结果与其他账号流程仍按各自验收记录展示。',
            'qq_pass': '邮件投递已完成；其他账号流程仍按各自验收记录展示。',
        },
        'empty': {
            'no_action': '当前无需你操作',
            'no_action_running': '员工正在继续处理任务，有新的测试版本时这里会提醒你。',
            'no_action_snapshot': '有新的测试版本时，这里会显示下一步操作。',
            'action_unknown': '暂未确认是否需要你操作',
            'action_unknown_hint': '请刷新状态，或查看已登记的操作说明。',
            'phase': '等待阶段信息更新', 'summary': '任务说明待更新',
            'standby_summary': '等待下一项任务安排', 'waiting_target': '测试版本生成后，这里会开放启动。',
            'none_target': '有需要你验收的版本时，会在这里显示。',
            'purpose': '请按已登记的测试说明完成验收。',
            'action_title': '请完成当前验收', 'action_sentence': '请查看当前操作说明，并按成功标准完成验收。',
            'unknown_resource': '暂未提供说明', 'unknown_value': '尚未提供',
        },
        'errors': {
            'TEST_RUNTIME_CONFIGURATION_INVALID': '冻结候选缺少有效的测试连接配置，已阻止启动。',
            'TEST_RUNTIME_TLS_INVALID': '测试证书与已批准回执不一致，已阻止启动。',
            'SOURCE_NOT_INTEGRATED': '构建源码不属于已批准的集成历史。',
            'BUILD_SOURCE_SUPERSEDED': '登记期间运行源码已变化，需要重新构建候选。',
            'TEST_DEVICE_BUSY': '测试设备仍在运行，请先关闭该设备窗口。',
            'TEST_DEVICE_PAIR_BUILD_CHANGED': '另一设备的版本不同或尚未确认，请先关闭两台设备再启动。',
            'TEST_DEVICE_CONTRACT_REQUIRED': '该候选尚未声明支持独立测试设备，已阻止启动。',
            'TEST_DEVICE_INTEGRATED_CANDIDATE_REQUIRED': '等待当前集成版本的精确测试候选。',
            'TEST_DEVICE_UNOWNED_ROOT': '目录不属于工作台，禁止启动或重置。',
            'TEST_DEVICE_UNSAFE_PATH': '测试目录未通过安全检查，操作已停止。',
            'TEST_DEVICE_SCENARIO_UNAVAILABLE': '该场景尚未实现，不能启动。',
            'ARTIFACT_HASH_MISMATCH': '这个文件不是当前指定测试版本。',
            'BUILD_IDENTITY_MISMATCH': '构建身份与指定候选不一致。',
            'SOURCE_IDENTITY_MISMATCH': '源码身份与指定候选不一致。',
            'UNTRUSTED_PATH': '文件不在受信任的本地产物目录，或路径包含别名。',
            'ARTIFACT_MISSING': '指定的测试文件尚不存在。',
            'OLD_OR_UNREADY_TARGET': '这是旧测试版本，或候选尚未准备完成。',
            'TARGET_CHANGED': '当前测试目标已更新，请刷新后再启动。',
            'CANCELLED': '已取消启动。', 'fallback': '无法完成操作，请查看工程详情。',
        },
        'messages': {
            'loading': '正在读取已登记的项目状态…', 'verifying': '正在校验测试版本，请稍候…',
            'launched': '测试版本已启动，请按测试说明完成验收。',
            'issues': '部分状态暂时无法确认，请查看工程详情。',
            'closing': '正在取消校验，请稍后关闭。',
            'old': '这是旧测试版本。历史记录只读，请先由负责人登记为当前测试版。',
            'current_history': '请使用主界面的启动按钮，启动前会重新校验版本。',
            'phase_schema_pending': '部署验收准备',
        },
        'devices': {
            'refresh': '刷新设备观测',
            'title': '跨设备测试', 'A': '设备 A：旧设备', 'B': '设备 B：新设备',
            'launch': '启动设备 {device}', 'reset': '重置测试设备 {device}', 'details': '查看设备详情',
            'cloud': '云端数据恢复', 'missing': '本机资源缺失', 'portable': '便携包（预留，尚未实现）',
            'waiting_sync': '等待云同步实现', 'waiting_observation': '等待设备观测',
            'scope': '以下为本地测试观测，不代表云同步或产品验收通过。',
            'account': '账号：{value}', 'root': '本地目录：{value}',
            'workspace': '服务端工作区：{value}', 'revision': '最近同步修订：{value}',
            'restored': '云端恢复实体数：{value}', 'missing_count': '缺少本地资源数：{value}',
            'unknown': '未观测', 'observed': '已观测（详情内查看）', 'unprepared': '启动时自动创建',
            'unowned': '目录未通过所有权检查',
            'build_wait': '等待支持设备隔离的集成候选', 'build_bound': '测试版本：同一集成候选，启动前校验文件',
            'reset_done': '测试设备已重置，已切换到新的本地凭据命名空间。',
            'launch_done': '测试设备已启动；请在设备内登录测试账号。',
        },
    }
}


class Resources:
    def __init__(self, locale='zh-CN', catalogs=None):
        self.locale = locale
        self.catalogs = CATALOGS if catalogs is None else catalogs

    def text(self, group, key, **values):
        fallback = CATALOGS['zh-CN']
        template = self.catalogs.get(self.locale, {}).get(group, {}).get(key)
        template = template or fallback.get(group, {}).get(key)
        if not template:
            template = (fallback['statuses']['UNKNOWN'] if group == 'statuses' else
                        fallback['errors']['fallback'] if group == 'errors' else fallback['empty']['unknown_resource'])
        try: return template.format(**values)
        except (KeyError, ValueError): return fallback['empty']['unknown_resource']


ICONS = {'PASS': '✓', 'PASS_STAGE': '✓', 'RUNNING': '●', 'BLOCKED': '!',
         'LOCKED': '○', 'NOT_RUN': '○', 'NOT_CREATED': '○', 'UNKNOWN': '?'}
COLORS = {'PASS': '#167047', 'PASS_STAGE': '#167047', 'RUNNING': '#2458d3',
          'BLOCKED': '#b42318', 'USER_ACTION_REQUIRED': '#b45309', 'READY': '#167047',
          'WAITING': '#b45309', 'WAITING_FOR_ARTIFACT': '#b45309'}
