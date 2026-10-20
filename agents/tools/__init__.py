# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
Shared tools for LTTM agents.

The Strands @tool functions the sub-agents call, and their helpers. athena_tool.py runs the SQL the Athena-based
sub-agents write; guardduty_history_tool.py and controltower_events_tool.py build their own SQL and run it on
Athena; the other *_tool.py files call AWS APIs directly with boto3.
"""
