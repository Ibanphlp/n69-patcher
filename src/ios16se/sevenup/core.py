"""Core utilities for SevenUp transplant engine.

Ported from SevenUP src/iphone7_port/core.py
"""

def require(cond, msg):
    if not cond:
        raise ValueError(msg)
