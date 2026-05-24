class WorkflowError(RuntimeError):
    pass


class ConfigError(WorkflowError):
    pass


class OpencodeError(WorkflowError):
    pass
