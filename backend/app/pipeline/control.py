"""Control-flow exceptions shared by the pipeline and long-running audio steps."""


class TaskCancelled(Exception):
    """Raised when the user asks to terminate the running task."""


class TaskPaused(Exception):
    """Raised when the user asks to pause; carries the stage to resume from."""

    def __init__(self, resume_stage: str) -> None:
        super().__init__(f"paused at {resume_stage}")
        self.resume_stage = resume_stage
