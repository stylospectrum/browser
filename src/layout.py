import skia  # type: ignore

from abc import ABC, abstractmethod
from typing import Union, cast

from css_parser import parse_transform
from node import Text, Element, Node
from draw_command import Blend, DrawRRect, DrawText, DrawLine, PaintCommand, Transform
from utils import get_font, linespace
from constants import INPUT_WIDTH_PX, BLOCK_ELEMENTS, V_STEP, H_STEP, WIDTH


def paint_visual_effects(node: Element, cmds: list, rect):
    opacity = float(node.style.get("opacity", "1.0"))
    blend_mode = node.style.get("mix-blend-mode")
    translation = parse_transform(
        node.style.get("transform", ""))

    if node.style.get("overflow", "visible") == "clip":
        border_radius = float(node.style.get(
            "border-radius", "0px")[:-2])
        if not blend_mode:
            blend_mode = "source-over"
        cmds.append(Blend(1.0, "destination-in", node, [
            DrawRRect(rect, border_radius, "white")
        ]))

    blend_op = Blend(opacity, blend_mode, node, cmds)
    node.blend_op = blend_op

    return [Transform(translation, rect, node, [blend_op])]


class Layout(ABC):
    def __init__(self) -> None:
        super().__init__()
        self.children: list[Layout] = []
        self.node: Node

    @abstractmethod
    def layout(self):
        pass

    @abstractmethod
    def paint(self):
        pass

    @abstractmethod
    def should_paint(self):
        pass

    @abstractmethod
    def paint_effects(self, cmds: list[PaintCommand]):
        pass

    @abstractmethod
    def __repr__(self):
        pass


class TextLayout(Layout):
    def __init__(self, node: Node, word: str, parent: 'LineLayout', previous: Union['TextLayout', None]):
        self.node = node
        self.word = word
        self.children: list = []
        self.parent = parent
        self.previous = previous
        self.y = 0

    def should_paint(self):
        return True

    def layout(self):
        weight = self.node.style["font-weight"]
        style = self.node.style["font-style"]
        if style == "normal":
            style = "roman"
        size = int(float(self.node.style["font-size"][:-2]) * .75)
        self.font = get_font(size, weight, style)

        self.width = self.font.measureText(self.word)

        if self.previous:
            space = self.previous.font.measureText(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

    def paint(self):
        color = self.node.style["color"]
        return [DrawText(self.x, self.y, self.word, self.font, color)]

    def paint_effects(self, cmds):
        return cmds

    def __repr__(self):
        return ("TextLayout(x={}, y={}, width={}, height={}, word={})").format(
            self.x, self.y, self.width, self.height, self.word)


class InputLayout(Layout):
    def __init__(self, node: Element, parent: 'LineLayout', previous: Union['InputLayout', None]):
        self.node = node
        self.children: list = []
        self.parent = parent
        self.previous = previous
        self.width = INPUT_WIDTH_PX
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0

    def should_paint(self):
        return True

    def self_rect(self):
        return skia.Rect.MakeLTRB(self.x, self.y,
                                  self.x + self.width, self.y + self.height)

    def layout(self):
        weight = self.node.style["font-weight"]
        style = self.node.style["font-style"]
        if style == "normal":
            style = "roman"
        size = int(float(self.node.style["font-size"][:-2]) * .75)
        self.font = get_font(size, weight, style)

        self.width = INPUT_WIDTH_PX

        if self.previous:
            space = self.previous.font.measureText(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

    def paint(self):
        cmds = []
        bgcolor = self.node.style.get("background-color",
                                      "transparent")
        if bgcolor != "transparent":
            radius = float(self.node.style.get("border-radius", "0px")[:-2])
            cmds.append(DrawRRect(self.self_rect(), radius, bgcolor))

        if self.node.tag == "input":
            text = self.node.attributes.get("value", "")
        elif self.node.tag == "button":
            if len(self.node.children) == 1 and \
               isinstance(self.node.children[0], Text):
                text = self.node.children[0].text
            else:
                print("Ignoring HTML contents inside button")
                text = ""

        if self.node.is_focused:
            cx = self.x + self.font.measureText(text)
            cmds.append(DrawLine(
                cx, self.y, cx, self.y + self.height, "black", 1))

        color = self.node.style["color"]
        cmds.append(
            DrawText(self.x, self.y, text, self.font, color))

        return cmds

    def paint_effects(self, cmds):
        return paint_visual_effects(self.node, cmds, self.self_rect())

    def __repr__(self):
        if self.node.tag == "input":
            extra = "type=input"
        else:
            extra = "type=button text={}".format(self.node.children[0].text)
        return "InputLayout(x={}, y={}, width={}, height={}, {})".format(
            self.x, self.y, self.width, self.height, extra)


class LineLayout:
    def __init__(self, node: Node, parent: 'BlockLayout', previous: Union['LineLayout', None]):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children: list[Union[TextLayout, InputLayout]] = []
        self.height = 0

    def should_paint(self):
        return True

    def layout(self):
        self.width = self.parent.width
        self.x = self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        if not self.children:
            self.height = 0
            return

        for word in self.children:
            word.layout()

        max_ascent = max([-word.font.getMetrics().fAscent
                          for word in self.children])
        baseline = self.y + 1.25 * max_ascent
        for word in self.children:
            word.y = baseline + word.font.getMetrics().fAscent
        max_descent = max([word.font.getMetrics().fDescent
                           for word in self.children])
        self.height = 1.25 * (max_ascent + max_descent)

    def paint(self):
        return []

    def paint_effects(self, cmds):
        return cmds

    def __repr__(self):
        return "LineLayout(x={}, y={}, width={}, height={})".format(
            self.x, self.y, self.width, self.height)


class BlockLayout:
    def __init__(self, node: Element, parent: 'BlockLayout', previous: Union['BlockLayout', None]):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children: list[Union[BlockLayout, LineLayout, InputLayout]] = []

        self.cursor_x = H_STEP
        self.cursor_y = V_STEP

        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0

    def layout_mode(self):
        if isinstance(self.node, Text):
            return "inline"
        elif any([isinstance(child, Element) and
                  child.tag in BLOCK_ELEMENTS
                  for child in self.node.children]):
            return "block"
        elif self.node.children or self.node.tag == "input":
            return "inline"
        else:
            return "block"

    def layout_intermediate(self):
        previous = None
        for child in self.node.children:
            next = BlockLayout(child, self, previous)
            self.children.append(next)
            previous = next

    def layout(self):
        self.x = self.parent.x
        self.width = self.parent.width

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        mode = self.layout_mode()
        if mode == "block":
            self.layout_intermediate()
        else:
            self.new_line()
            self.recurse(self.node)

        for child in self.children:
            child.layout()

        self.height = sum([
            child.height for child in self.children])

    def new_line(self):
        self.cursor_x = 0
        last_line = self.children[-1] if self.children else None
        new_line = LineLayout(self.node, self, last_line)
        self.children.append(new_line)

    def word(self, node: Node, word: str):
        weight = node.style["font-weight"]
        style = node.style["font-style"]
        if style == "normal":
            style = "roman"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)

        w = font.measureText(word)
        if self.cursor_x + w > self.width:
            self.new_line()

        line = cast(LineLayout, self.children[-1])
        previous_word = line.children[-1] if line.children else None
        text = TextLayout(node, word, line, cast(
            Union[TextLayout, None], previous_word))
        line.children.append(text)
        self.cursor_x += w + font.measureText(" ")

    def input(self, node: Element):
        w = INPUT_WIDTH_PX
        if self.cursor_x + w > self.width:
            self.new_line()
        line = cast(LineLayout, self.children[-1])
        previous_word = line.children[-1] if line.children else None
        input = InputLayout(node, line, cast(
            Union[InputLayout, None], previous_word))
        line.children.append(input)

        weight = node.style["font-weight"]
        style = node.style["font-style"]
        if style == "normal":
            style = "roman"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)

        self.cursor_x += w + font.measureText(" ")

    def recurse(self, node: Node):
        if isinstance(node, Text):
            for word in node.text.split():
                self.word(node, word)
        else:
            if node.tag == "br":
                self.new_line()
            elif node.tag == "input" or node.tag == "button":
                self.input(node)
            else:
                for child in node.children:
                    self.recurse(child)

    def self_rect(self):
        return skia.Rect.MakeLTRB(self.x, self.y,
                                  self.x + self.width, self.y + self.height)

    def paint(self):
        cmds: list[PaintCommand] = []
        bgcolor = self.node.style.get("background-color",
                                      "transparent")

        if bgcolor != "transparent":
            radius = float(self.node.style.get("border-radius", "0px")[:-2])
            cmds.append(DrawRRect(self.self_rect(), radius, bgcolor))

        return cmds

    def should_paint(self):
        return isinstance(self.node, Text) or \
            (self.node.tag != "input" and self.node.tag != "button")

    def paint_effects(self, cmds):
        cmds = paint_visual_effects(
            self.node, cmds, self.self_rect())
        return cmds

    def __repr__(self):
        return "BlockLayout[{}](x={}, y={}, width={}, height={})".format(
            self.layout_mode(), self.x, self.y, self.width, self.height)


class DocumentLayout(Layout):
    def __init__(self, node: Node):
        self.node = node
        self.parent = None
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = None

    def layout(self):
        self.width = WIDTH - 2 * H_STEP
        self.x = H_STEP
        self.y = V_STEP
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout()
        self.height = child.height

    def paint(self):
        return []

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        return cmds

    def __repr__(self):
        return "DocumentLayout()"
