# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
The Strands hook providers of the agent layer.

The supervisor's hooks guard its answers and keep its memory (architecture_guard_hook.py,
output_integrity_hook.py, memory_hook.py); the Athena sub-agents' hooks check and rewrite their SQL
before it runs (sql_validator_hook.py, sql_rewrite_hook.py).
"""
