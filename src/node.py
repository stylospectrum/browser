from typing import Union, TYPE_CHECKING

if TYPE_CHECKING:
    from css_parser import Animation


class Text:
    def __init__(self, text: str, parent: Union['Node', None]):
        self.text = text
        self.children: list[Node] = []
        self.parent = parent
        self.style: dict[str, str] = {}
        self.is_focused = False
        self.animations: dict[str, 'Animation'] = {}

    def __repr__(self):
        return repr(self.text)


class Element:
    def __init__(self, tag: str, attributes: dict[str, str], parent: Union['Node', None]):
        self.tag = tag
        self.attributes = attributes
        self.children: list[Node] = []
        self.parent = parent
        self.style: dict[str, str] = {}
        self.is_focused = False
        self.animations: dict[str, 'Animation'] = {}

    def __repr__(self):
        return "<" + self.tag + ">"


Node = Element | Text
