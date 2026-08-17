class VoiceCloneFlowError(RuntimeError):
    """Base error for expected pipeline failures."""


class PipelineConfigurationError(VoiceCloneFlowError):
    """Raised when a required external runtime is unavailable."""


class MediaProcessingError(VoiceCloneFlowError):
    """Raised when media conversion or analysis fails."""
