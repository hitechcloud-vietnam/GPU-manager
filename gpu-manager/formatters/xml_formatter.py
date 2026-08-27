import xml.etree.ElementTree as ET
import xml.dom.minidom
from .base import BaseFormatter

class XmlFormatter(BaseFormatter):
    def __init__(self, pretty: bool = False):
        self.pretty = pretty

    def _dict_to_elem(self, tag: str, d: any) -> ET.Element:
        elem = ET.Element(tag)
        if isinstance(d, dict):
            for key, val in d.items():
                if isinstance(val, list):
                    list_elem = ET.SubElement(elem, key)
                    item_tag = key[:-1] if key.endswith('s') else "item"
                    for item in val:
                        list_elem.append(self._dict_to_elem(item_tag, item))
                elif isinstance(val, dict):
                    elem.append(self._dict_to_elem(key, val))
                else:
                    sub = ET.SubElement(elem, key)
                    sub.text = str(val) if val is not None else ""
        else:
            elem.text = str(d) if d is not None else ""
        return elem

    def _format(self, data: dict, root_tag: str = "response") -> str:
        root = self._dict_to_elem(root_tag, data)
        raw_xml = ET.tostring(root, encoding="utf-8").decode("utf-8")
        if self.pretty:
            dom = xml.dom.minidom.parseString(raw_xml)
            return dom.toprettyxml(indent="  ")
        return raw_xml

    def format_status(self, data: dict) -> str:
        return self._format(data, "gpu_status_response")

    def format_profiles(self, data: dict) -> str:
        return self._format(data, "gpu_profiles_response")

    def format_action_result(self, data: dict) -> str:
        return self._format(data, "action_result")
