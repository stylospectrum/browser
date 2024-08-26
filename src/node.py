from typing import Union, TYPE_CHECKING, Any

from protected_field import ProtectedField

if TYPE_CHECKING:
    from css_parser import Animation
    from layout import Blend
    from frame import Frame


class Text:
    def __init__(self, text: str, parent: 'Element'):
        self.text = text
        self.children: list[Node] = []
        self.parent = parent
        self.is_focused = False
        self.animations: dict[str, 'Animation'] = {}
        self.layout_object: Any = None
        self.style = ProtectedField()

    def __repr__(self):
        return repr(self.text)


class Element:
    def __init__(self, tag: str, attributes: dict[str, str], parent: Union['Element', None]):
        self.tag = tag
        self.attributes = attributes
        self.children: list[Node] = []
        self.parent = parent
        self.style = ProtectedField()
        self.is_focused = False
        self.animations: dict[str, 'Animation'] = {}
        self.blend_op: Union['Blend', None] = None
        self.layout_object: Any = None
        self.encoded_data = None
        self.image: Any
        self.frame: Union['Frame', None] = None

    def __repr__(self):
        return "<" + self.tag + ">"


Node = Element | Text
