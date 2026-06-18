class AppException(Exception):
    code: int
    detail: str

    def __init__(self, code: int, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class TaskNotFoundException(AppException):
    def __init__(self, task_id: int):
        super().__init__(404, f"任务不存在: task_id={task_id}")


class UserNotFoundException(AppException):
    def __init__(self, user_id: int):
        super().__init__(404, f"用户不存在: user_id={user_id}")


class InvalidStatusTransitionException(AppException):
    def __init__(self, task_id: int, from_status: str, to_status: str, action: str):
        super().__init__(
            400,
            f"非法状态变更: task_id={task_id}, 当前状态={from_status}, "
            f"目标状态={to_status}, 操作={action}"
        )


class DuplicateOperationException(AppException):
    def __init__(self, task_id: int, action: str, user_id: int):
        super().__init__(
            409,
            f"重复操作: task_id={task_id}, 操作={action}, user_id={user_id}, "
            f"该用户已执行过此操作"
        )


class PermissionDeniedException(AppException):
    def __init__(self, user_id: int, action: str, task_id: int, reason: str):
        super().__init__(
            403,
            f"无权限操作: user_id={user_id}, 操作={action}, task_id={task_id}, 原因={reason}"
        )


class ValidationException(AppException):
    def __init__(self, field: str, detail: str):
        super().__init__(422, f"参数校验失败: 字段={field}, {detail}")


class FilterNotFoundException(AppException):
    def __init__(self, filter_id: int):
        super().__init__(404, f"过滤条件不存在: filter_id={filter_id}")


class DelegationNotFoundException(AppException):
    def __init__(self, delegation_id: int):
        super().__init__(404, f"委托不存在: delegation_id={delegation_id}")


class TemplateNotFoundException(AppException):
    def __init__(self, template_id: int):
        super().__init__(404, f"模板不存在: template_id={template_id}")


class InvalidDelegationException(AppException):
    def __init__(self, detail: str):
        super().__init__(400, f"无效委托: {detail}")


class BatchApprovalException(AppException):
    def __init__(self, detail: str, failed_tasks: list[int] = None):
        self.failed_tasks = failed_tasks or []
        super().__init__(400, f"批量审批失败: {detail}")


class TaskNotOverdueException(AppException):
    def __init__(self, task_id: int):
        super().__init__(400, f"任务未超时: task_id={task_id}")


class AlreadyEscalatedException(AppException):
    def __init__(self, task_id: int):
        super().__init__(400, f"任务已升级: task_id={task_id}")
