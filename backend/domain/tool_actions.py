"""Versioned tool vocabulary shared with the frontend, never an IO mapping."""
import json
from pathlib import Path
from typing import Literal

_contract = json.loads((Path(__file__).resolve().parents[2] / 'contracts/tool-actions.json').read_text(encoding='utf-8'))
if _contract['version'] != 1:
    raise ValueError('Unsupported tool action contract version')
TOOL_ACTION_DEFINITIONS = tuple(_contract['actions'])
TOOL_ACTIONS = frozenset(row['id'] for row in TOOL_ACTION_DEFINITIONS)
ToolActionName = Literal[tuple(row['id'] for row in TOOL_ACTION_DEFINITIONS)]
ACTION_ALIASES = {row['zh']: row['id'].upper() for row in TOOL_ACTION_DEFINITIONS}
