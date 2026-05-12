"""RCA bot — generate root-cause narratives for approval_queue items.

When the retry orchestrator exhausts attempts on a workflow, it writes
an ``approval_queue`` row with ``action_kind='retry_exhausted'``. The
RCA bot picks those up, gathers recent events + findings for the
workflow, asks the configured LLM for a narrative explanation, and
writes the result back to the queue row's ``narrative`` column.

Output goes into the approval_queue row, NOT gap_reports — RCAs are
HITL artifacts (the operator reads + confirms), not new findings.
"""

from .audit import AuditResult, sweep

__all__ = ["AuditResult", "sweep"]
