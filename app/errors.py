class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class ConversationTransferred(Exception):
    """会话已转人工，助手不能再介入（顾客消息由坐席回复）。"""
