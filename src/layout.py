import skia

from typing import Union, cast, TYPE_CHECKING, Any

from css_parser import parse_transform
from node import Text, Element, Node
from draw_command import Blend, DrawRRect, DrawText, DrawLine, PaintCommand, Transform, DrawOutline, DrawImage
from utils import linespace, dpx, parse_outline, font, tree_to_list
from protected_field import ProtectedField
from constants import INPUT_WIDTH_PX, BLOCK_ELEMENTS, V_STEP, H_STEP, IFRAME_HEIGHT_PX, IFRAME_WIDTH_PX

if TYPE_CHECKING:
    from frame import Frame


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


def paint_outline(node: Node, cmds: list[PaintCommand], rect, zoom: float):
    outline = parse_outline(node.style.get("outline"))
    if not outline:
        return
    thickness, color = outline
    cmds.append(DrawOutline(rect, color, dpx(thickness, zoom)))


def DrawCursor(elt: Union['TextLayout', 'BlockLayout'], offset: float):
    x = elt.x + offset
    return DrawLine(x, elt.y, x, elt.y + elt.height, "red", 1)


class TextLayout:
    def __init__(self, node: Node, word: str, parent: 'LineLayout', previous: Union['TextLayout', None]):
        self.node = node
        self.word = word
        self.children: list = []
        self.parent = parent
        self.previous = previous
        self.y = 0.0
        self.x = 0.0
        self.height = 0.0
        self.zoom: float

    def should_paint(self):
        return True

    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x, self.y, self.x + self.width,
            self.y + self.height)

    def layout(self) -> None:
        self.zoom = self.parent.zoom.get()
        self.font = font(self.node.style, self.zoom)

        # Do not set self.y!!!
        self.width = self.font.measureText(self.word)

        if self.previous:
            space = self.previous.font.measureText(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

        self.ascent = self.font.getMetrics().fAscent * 1.25
        self.descent = self.font.getMetrics().fDescent * 1.25

    def paint(self):
        color = self.node.style["color"]
        return [DrawText(self.x, self.y, self.word, self.font, color)]

    def paint_effects(self, cmds):
        return cmds

    def __repr__(self):
        return ("TextLayout(x={}, y={}, width={}, height={}, word={})").format(
            self.x, self.y, self.width, self.height, self.word)


class EmbedLayout:
    def __init__(self, node: Element, parent: 'LineLayout', previous: Union['EmbedLayout', None], frame: 'Frame'):
        self.node = node
        self.frame = frame
        self.children: list = []
        self.parent = parent
        self.previous = previous
        self.width = INPUT_WIDTH_PX
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.node.layout_object = self

    def should_paint(self):
        return True

    def layout(self):
        self.zoom = self.parent.zoom
        self.font = font(self.node.style, self.zoom)
        if self.previous:
            space = self.previous.font.measureText(" ")
            self.x = \
                self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x


class InputLayout(EmbedLayout):
    def __init__(self, node: Element, parent: 'LineLayout', previous: Union['InputLayout', None], frame: 'Frame'):
        super().__init__(node, parent, previous, frame)

    def layout(self):
        super().layout()

        self.width = dpx(INPUT_WIDTH_PX, self.zoom)
        self.height = linespace(self.font)
        self.ascent = -self.height
        self.descent = 0

    def self_rect(self):
        return skia.Rect.MakeLTRB(self.x, self.y,
                                  self.x + self.width, self.y + self.height)

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

        if self.node.is_focused and self.node.tag == "input":
            cmds.append(DrawCursor(self, self.font.measureText(text)))

        color = self.node.style["color"]
        cmds.append(
            DrawText(self.x, self.y, text, self.font, color))

        return cmds

    def paint_effects(self, cmds: list[PaintCommand]):
        cmds = paint_visual_effects(self.node, cmds, self.self_rect())
        paint_outline(self.node, cmds, self.self_rect(), self.zoom)
        return cmds

    def __repr__(self):
        if self.node.tag == "input":
            extra = "type=input"
        else:
            extra = "type=button text={}".format(self.node.children[0].text)
        return "InputLayout(x={}, y={}, width={}, height={}, {})".format(
            self.x, self.y, self.width, self.height, extra)


class ImageLayout(EmbedLayout):
    def __init__(self, node: Element, parent: 'LineLayout', previous: Union['ImageLayout', None], frame: 'Frame'):
        super().__init__(node, parent, previous, frame)

    def layout(self):
        super().layout()

        width_attr = self.node.attributes.get("width")
        height_attr = self.node.attributes.get("height")
        image_width = self.node.image.width()
        image_height = self.node.image.height()
        aspect_ratio = image_width / image_height

        if width_attr and height_attr:
            self.width = dpx(int(width_attr), self.zoom)
            self.img_height = dpx(int(height_attr), self.zoom)
        elif width_attr:
            self.width = dpx(int(width_attr), self.zoom)
            self.img_height = self.width / aspect_ratio
        elif height_attr:
            self.img_height = dpx(int(height_attr), self.zoom)
            self.width = self.img_height * aspect_ratio
        else:
            self.width = dpx(image_width, self.zoom)
            self.img_height = dpx(image_height, self.zoom)

        self.height = max(self.img_height, linespace(self.font))

        self.ascent = -self.height
        self.descent = 0

    def paint(self):
        cmds = []
        rect = skia.Rect.MakeLTRB(
            self.x, self.y + self.height - self.img_height,
            self.x + self.width, self.y + self.height)
        quality = self.node.style.get("image-rendering", "auto")
        cmds.append(DrawImage(self.node.image, rect, quality))
        return cmds

    def paint_effects(self, cmds):
        return cmds

    def __repr__(self):
        return ("ImageLayout(src={}, x={}, y={}, width={}," +
                "height={})").format(self.node.attributes["src"],
                                     self.x, self.y, self.width, self.height)


class IframeLayout(EmbedLayout):
    def __init__(self, node, parent, previous, parent_frame):
        super().__init__(node, parent, previous, parent_frame)

    def layout(self):
        super().layout()

        width_attr = self.node.attributes.get("width")
        height_attr = self.node.attributes.get("height")

        if width_attr:
            self.width = dpx(int(width_attr) + 2, self.zoom)
        else:
            self.width = dpx(IFRAME_WIDTH_PX + 2, self.zoom)

        if height_attr:
            self.height = dpx(int(height_attr) + 2, self.zoom)
        else:
            self.height = dpx(IFRAME_HEIGHT_PX + 2, self.zoom)

        self.node.frame.frame_height = \
            self.height - dpx(2, self.zoom)
        self.node.frame.frame_width = \
            self.width - dpx(2, self.zoom)

        self.ascent = -self.height
        self.descent = 0

    def paint(self):
        cmds = []

        rect = skia.Rect.MakeLTRB(
            self.x, self.y,
            self.x + self.width, self.y + self.height)
        bgcolor = self.node.style.get("background-color",
                                      "transparent")
        if bgcolor != "transparent":
            radius = dpx(float(
                self.node.style.get("border-radius", "0px")[:-2]),
                self.zoom)
            cmds.append(DrawRRect(rect, radius, bgcolor))
        return cmds

    def paint_effects(self, cmds):
        rect = skia.Rect.MakeLTRB(
            self.x, self.y,
            self.x + self.width, self.y + self.height)
        diff = dpx(1, self.zoom)
        offset = (self.x + diff, self.y + diff)
        cmds = [Transform(offset, rect, self.node, cmds)]
        inner_rect = skia.Rect.MakeLTRB(
            self.x + diff, self.y + diff,
            self.x + self.width - diff, self.y + self.height - diff)
        internal_cmds = cmds
        internal_cmds.append(Blend(1.0, "destination-in", None, [
            DrawRRect(inner_rect, 0, "white")]))
        cmds = [Blend(1.0, "source-over", self.node, internal_cmds)]
        paint_outline(self.node, cmds, rect, self.zoom)
        cmds = paint_visual_effects(self.node, cmds, rect)
        return cmds

    def __repr__(self):
        return "IframeLayout(src={}, x={}, y={}, width={}, height={})".format(
            self.node.attributes["src"], self.x, self.y, self.width, self.height)


class LineLayout:
    def __init__(self, node: Node, parent: 'BlockLayout', previous: Union['LineLayout', None]):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children: list[Union[TextLayout, InputLayout]] = []
        self.height = 0
        self.x: float
        self.y: float
        self.zoom = ProtectedField()

    def should_paint(self):
        return True

    def layout(self) -> None:
        self.zoom.copy(self.parent.zoom)
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

        max_ascent = max([-child.ascent
                          for child in self.children])
        baseline = self.y + max_ascent

        for child in self.children:
            if isinstance(child, TextLayout):
                child.y = baseline + child.ascent / 1.25
            else:
                child.y = baseline + child.ascent
        max_descent = max([child.descent
                           for child in self.children])
        self.height = max_ascent + max_descent

    def paint(self):
        return []

    def paint_effects(self, cmds: list[PaintCommand]):
        outline_rect = skia.Rect.MakeEmpty()
        outline_node = None
        for child in self.children:
            outline_str = cast(Element, child.node.parent).style.get("outline")
            if parse_outline(outline_str):
                outline_rect.join(child.self_rect())
                outline_node = child.node.parent
        if outline_node:
            paint_outline(
                outline_node, cmds, outline_rect, self.zoom.get())
        return cmds

    def __repr__(self):
        return "LineLayout(x={}, y={}, width={}, height={})".format(
            self.x, self.y, self.width, self.height)


class BlockLayout:
    def __init__(self, node: Element, parent: Union['BlockLayout', 'DocumentLayout'], previous: Union['BlockLayout', None], frame: 'Frame'):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = ProtectedField()
        self.zoom = ProtectedField()
        self.node.layout_object = self
        self.frame = frame

        self.cursor_x = 0.0
        self.x = 0.0
        self.y = 0.0
        self.width = ProtectedField()
        self.height = 0.0

    def layout_mode(self):
        if isinstance(self.node, Text):
            return "inline"
        elif self.node.children:
            for child in self.node.children:
                if isinstance(child, Text):
                    continue
                if child.tag in BLOCK_ELEMENTS:
                    return "block"
            return "inline"
        elif self.node.tag in ["input", "img", "iframe"]:
            return "inline"
        else:
            return "block"

    def layout(self):
        self.zoom.copy(self.parent.zoom)
        self.width.copy(self.parent.width)
        self.x = self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        mode = self.layout_mode()
        if mode == "block":
             if self.children.dirty:
                children = []
                previous = None
                for child in self.node.children:
                    next = BlockLayout(
                        child, self, previous, self.frame)
                    children.append(next)
                    previous = next
                self.children.set(children)
        else:
            self.new_line()
            self.recurse(self.node)

        for child in self.children.get():
            child.layout()

        self.height = sum([
            child.height for child in self.children.get()])

    def add_inline_child(self, node: Node, w: float, child_class, frame: 'Frame', word=None):
        zoom = self.zoom.read(notify=self.children)
        width = self.width.read(notify=self.children)
        if self.cursor_x + w > self.x + width:
            self.new_line()
        line = cast(LineLayout, self.children.get()[-1])
        previous_word = line.children[-1] if line.children else None
        if word:
            child = child_class(node, word, line, previous_word)
        else:
            child = child_class(node, line, previous_word, frame)
        line.children.append(child)
        self.cursor_x += w + \
            font(node.style, zoom).measureText(" ")

    def new_line(self):
        self.cursor_x = self.x
        last_line = self.children[-1] if self.children else None
        new_line = LineLayout(self.node, self, last_line)
        self.children.append(new_line)

    def word(self, node: Text, word: str):
        zoom = self.zoom.read(notify=self.children)
        node_font = font(node.style, zoom)
        w = node_font.measureText(word)
        self.add_inline_child(node, w, TextLayout, self.frame, word)

    def input(self, node: Element):
        zoom = self.zoom.read(notify=self.children)
        w = dpx(INPUT_WIDTH_PX, zoom)
        self.add_inline_child(node, w, InputLayout, self.frame)

    def image(self, node: Element):
        zoom = self.zoom.read(notify=self.children)
        if "width" in node.attributes:
            w = dpx(int(node.attributes["width"]), zoom)
        else:
            w = dpx(node.image.width(), zoom)
        self.add_inline_child(node, w, ImageLayout, self.frame)

    def iframe(self, node: Element):
        zoom = self.zoom.read(notify=self.children)
        if "width" in self.node.attributes:
            w = dpx(int(self.node.attributes["width"]),
                    zoom)
        else:
            w = IFRAME_WIDTH_PX + dpx(2, zoom)
        self.add_inline_child(node, w, IframeLayout, self.frame)

    def recurse(self, node: Node):
        if isinstance(node, Text):
            for word in node.text.split():
                self.word(node, word)
        else:
            if node.tag == "br":
                self.new_line()
            elif node.tag == "input" or node.tag == "button":
                self.input(node)
            elif node.tag == "img":
                self.image(node)
            elif node.tag == "iframe" and \
                    "src" in node.attributes:
                self.iframe(node)
            else:
                for child in node.children:
                    self.recurse(child)

    def self_rect(self):
        return skia.Rect.MakeLTRB(self.x, self.y,
                                  self.x + self.width, self.y + self.height)

    def paint(self) -> list[PaintCommand]:
        cmds: list[PaintCommand] = []
        bgcolor = self.node.style.get("background-color",
                                      "transparent")

        if bgcolor != "transparent":
            radius = float(self.node.style.get("border-radius", "0px")[:-2])
            cmds.append(DrawRRect(self.self_rect(), radius, bgcolor))

        if self.node.is_focused \
                and "contenteditable" in self.node.attributes:
            text_nodes = [
                t for t in tree_to_list(self, [])
                if isinstance(t, TextLayout)
            ]
            if text_nodes:
                cmds.append(DrawCursor(text_nodes[-1],
                                       text_nodes[-1].width))
            else:
                cmds.append(DrawCursor(self, 0))

        return cmds

    def should_paint(self):
        return isinstance(self.node, Text) or \
            (self.node.tag not in
                ["input", "button", "img", "iframe"])

    def paint_effects(self, cmds):
        cmds = paint_visual_effects(
            self.node, cmds, self.self_rect())
        return cmds

    def __repr__(self):
        return "BlockLayout[{}](x={}, y={}, width={}, height={})".format(
            self.layout_mode(), self.x, self.y, self.width, self.height)


class DocumentLayout:
    def __init__(self, node: Element, frame: 'Frame'):
        self.node = node
        self.frame = frame
        self.parent = None
        self.children: list = []
        self.x: Union[float, None] = None
        self.y: Union[float, None] = None
        self.width = ProtectedField()
        self.height: Union[float, None] = None
        self.node.layout_object = self
        self.zoom = ProtectedField()

    def layout(self, width: float, zoom: float):
        self.zoom.set(zoom)
        self.width.set(width - 2 * dpx(H_STEP, zoom))

        if not self.children:
            child = BlockLayout(self.node, self, None, self.frame)
        else:
            child = self.children[0]
        self.children = [child]

        self.x = dpx(H_STEP, zoom)
        self.y = dpx(V_STEP, zoom)
        child.layout()
        self.height = child.height

    def paint(self):
        return []

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        if self.frame != self.frame.tab.root_frame and self.frame.scroll != 0:
            rect = skia.Rect.MakeLTRB(
                self.x, self.y,
                self.x + self.width, self.y + self.height)
            cmds = [Transform((0, - self.frame.scroll), rect, self.node, cmds)]
        return cmds

    def __repr__(self):
        return "DocumentLayout()"
