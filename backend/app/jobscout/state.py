"""JobScout-only checkpoint channels; no generic goal/autonomy channels."""

from langchain.agents import AgentState

SERVER_STATE_KEYS = frozenset({"jobscout_anchor", "jobscout_decision"})


class JobScoutState(AgentState):
    jobscout_anchor: dict
    jobscout_decision: dict
