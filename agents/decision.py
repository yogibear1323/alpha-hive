"""Alias for the Queen Bot (agents/queen.py), kept so older imports keep working.

The Decision Bot is now the Queen: the boss of the hive who owns the round and makes the final
call. She still cannot override or bypass the Risk gate.
"""
from agents.queen import NAME, SCHEMA, run  # noqa: F401
