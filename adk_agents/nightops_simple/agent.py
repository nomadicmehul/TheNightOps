"""ADK Web wrapper for TheNightOps Simple Agent (Plan B).

Usage:
    source config/.env && adk web adk_agents/

TODO: Fix model resolution — preview models (gemini-3.1-pro-preview)
      don't resolve via adk web's public API path. Need to either
      use a GA model or configure the API endpoint correctly.
"""

from nightops.core.config import NightOpsConfig
from nightops.agents.simple_agent import create_simple_agent

config = NightOpsConfig()
root_agent = create_simple_agent(config)
