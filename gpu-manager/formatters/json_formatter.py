import json
from .base import BaseFormatter

class JsonFormatter(BaseFormatter):
    def __init__(self, pretty: bool = False):
        self.pretty = pretty

    def _to_json(self, data: dict) -> str:
        if self.pretty:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return json.dumps(data, ensure_ascii=False)

    def format_status(self, data: dict) -> str:
        return self._to_json(data)

    def format_profiles(self, data: dict) -> str:
        return self._to_json(data)

    def format_action_result(self, data: dict) -> str:
        return self._to_json(data)
