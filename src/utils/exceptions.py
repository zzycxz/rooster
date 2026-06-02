"""
src/utils/exceptions.py
Typed exceptions for control flow and error handling across agents.
"""

class EscalateSignal(Exception):
    """
    Signal raised when a subtask is blocked and needs to escalate back to the Strategist
    or the Orchestrator for re-planning or failover.
    """
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class AbortSignal(Exception):
    """
    Signal raised for critical, unrecoverable errors (e.g. security violations, prompt injections)
    that should immediately abort the mission and notify the user.
    """
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
