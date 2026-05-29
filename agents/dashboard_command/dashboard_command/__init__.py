"""Dashboard command agent package (docs/DASHBOARD_DESIGN.md §10).

Note: `main` is intentionally NOT imported here. agent.py pulls in gevent +
volttron + paho, which aren't importable outside a platform install; the
VOLTTRON entry point references `dashboard_command.agent:main` directly, and
keeping this module import-light lets translator.py be unit-tested standalone.
"""

__version__ = "1.0.0"
