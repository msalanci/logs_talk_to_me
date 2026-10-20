# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
untrusted_text.py — Values from logs and AWS kept on one line in the results' text.

The tools print results as text with their own layout ("Row 2:", "  eventname: …", "--- Finding ---").
A value with a line break in it could continue that layout: a CloudWatch message carrying
"\\n\\nRow 2:\\n  eventname: DeleteBucket" printed as a well-formed extra row. one_line() shows every line
break inside a value as a visible escape (\\n, \\r, \\u2028, …), so no value can start a line of the layout.
"""

import functools
import re

_BREAKS = {"\r\n": "\\r\\n", "\n": "\\n", "\r": "\\r", "\v": "\\v", "\f": "\\f", "\x1c": "\\x1c", "\x1d": "\\x1d",
           "\x1e": "\\x1e", "\x85": "\\x85", " ": "\\u2028", " ": "\\u2029"}
_PATTERN = re.compile("|".join(re.escape(b) for b in sorted(_BREAKS, key=len, reverse=True)))


def one_line(value) -> str:
    """The value as text on one line: each line break in it written as its escape.

    Args:
        value: Any value; it is turned into text first.

    Returns:
        The text, with \\n, \\r, \\u2028 and the other line breaks of _BREAKS written as escapes.
    """
    return _PATTERN.sub(lambda m: _BREAKS[m.group(0)], str(value))


def one_line_all(value):
    """one_line on every string inside a dict or list (an AWS response); other values unchanged.

    Args:
        value: A string, a dict, a list or tuple, or any other value, at any depth.

    Returns:
        The same shape, every string on one line; dict keys are not changed.
    """
    if isinstance(value, str):
        return one_line(value)
    if isinstance(value, dict):
        return {key: one_line_all(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(one_line_all(item) for item in value)
    return value


def one_line_args(format_fn):
    """Decorator for a result formatter: every string in its arguments is put on one line first.

    Args:
        format_fn: The formatter, such as a tool's _format_finding.

    Returns:
        The wrapped formatter, with the same name and docstring.
    """
    @functools.wraps(format_fn)
    def wrapper(*args, **kwargs):
        """Call the formatter with every string of its arguments on one line.

        Args:
            *args: The formatter's positional arguments.
            **kwargs: The formatter's keyword arguments.

        Returns:
            What the formatter returns.
        """
        return format_fn(*one_line_all(args), **one_line_all(kwargs))
    return wrapper
