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
    opacity = float(node.style["opacity"].get())
    blend_mode = node.style["mix-blend-mode"].get()
    translation = parse_transform(
        node.style["transform"].get())

    if node.style["overflow"].get() == "clip":
        border_radius = float(node.style["border-radius"].get()[:-2])
        if not blend_mode:
            blend_mode = "source-over"
        cmds.append(Blend(1.0, "destination-in", node, [
            DrawRRect(rect, border_radius, "white")
        ]))

    blend_op = Blend(opacity, blend_mode, node, cmds)
    node.blend_op = blend_op

    return [Transform(translation, rect, node, [blend_op])]


def paint_outline(node: Node, cmds: list[PaintCommand], rect, zoom: float):
    outline = parse_outline(node.style["outline"].get())
    if not outline:
        return
    thickness, color = outline
    cmds.append(DrawOutline(rect, color, dpx(thickness, zoom)))


def DrawCursor(elt: Union['TextLayout', 'BlockLayout'], offset: float):
    x = elt.x.get() + offset
    return DrawLine(x, elt.y.get(), x, elt.y.get() + elt.height.get(), "red", 1)


class TextLayout:
    def __init__(self, node: Node, word: str, parent: 'LineLayout', previous: Union['TextLayout', None]):
        self.node = node
        self.word = word
        self.children: list = []
        self.parent = parent
        self.previous = previous
        self.x = ProtectedField(self, 'x')
        self.y = ProtectedField(self, 'y')
        self.height = ProtectedField(self, 'height')
        self.width = ProtectedField(self, 'width')
        self.zoom = ProtectedField(self, 'zoom')
        self.font = ProtectedField(self, 'font')
        self.ascent = ProtectedField(self, 'ascent')
        self.descent = ProtectedField(self, 'descent')

    def should_paint(self):
        return True

    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x, self.y, self.x + self.width,
            self.y + self.height)

    def layout(self) -> None:
        self.zoom.copy(self.parent.zoom)
        zoom = self.zoom.read(notify=self.font)
        self.font.set(font(
            self.node.style, zoom, notify=self.font))

        f = self.font.read(notify=self.width)
        self.width.set(f.measureText(self.word))

        if self.previous:
            prev_x = self.previous.x.read(notify=self.x)
            prev_font = self.previous.font.read(notify=self.x)
            prev_width = self.previous.width.read(notify=self.x)
            self.x.set(
                prev_x + prev_font.measureText(' ') + prev_width)
        else:
            self.x.copy(self.parent.x)

        f = self.font.read(notify=self.ascent)
        self.ascent.set(f.getMetrics().fAscent * 1.25)

        f = self.font.read(notify=self.descent)
        self.descent.set(f.getMetrics().fDescent * 1.25)

        f = self.font.read(notify=self.height)
        self.height.set(linespace(f) * 1.25)

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
        self.width = ProtectedField(self, 'width')
        self.zoom = ProtectedField(self, 'zoom')
        self.x = ProtectedField(self, 'x')
        self.y = ProtectedField(self, 'y')
        self.height = ProtectedField(self, 'height')
        self.ascent = ProtectedField(self, 'ascent')
        self.descent = ProtectedField(self, 'descent')
        self.node.layout_object = self

    def should_paint(self):
        return True

    def layout(self):
        self.zoom.copy(self.parent.zoom)
        zoom = self.zoom.read(notify=self.width)
        style = self.node.style.read(notify=self.width)
        self.font = font(style, zoom)

        if self.previous:
            prev_x = self.previous.x.read(notify=self.x)
            prev_font = self.previous.font.read(notify=self.x)
            prev_width = self.previous.width.read(notify=self.x)
            self.x.set(prev_x + prev_font.measureText(' ') + prev_width)
        else:
            self.x.copy(self.parent.x)


class InputLayout(EmbedLayout):
    def __init__(self, node: Element, parent: 'LineLayout', previous: Union['InputLayout', None], frame: 'Frame'):
        super().__init__(node, parent, previous, frame)

    def layout(self):
        super().layout()
        zoom = self.zoom.read(notify=self.width)
        self.width.set(dpx(INPUT_WIDTH_PX, zoom))

        font = self.font.read(notify=self.height)
        self.height.set(linespace(font))

        height = self.height.read(notify=self.ascent)
        self.ascent.set(-height)
        self.descent.set(0)

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

        font = self.font.read(notify=self.height)
        self.height.set(max(self.img_height, linespace(font)))

        height = self.height.read(notify=self.ascent)
        self.ascent.set(-height)
        self.descent.set(0)

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

        w_zoom = self.zoom.read(notify=self.width)
        if width_attr:
            self.width.set(dpx(int(width_attr) + 2, w_zoom))
        else:
            self.width.set(dpx(IFRAME_WIDTH_PX + 2, w_zoom))

        h_zoom = self.zoom.read(notify=self.height)
        if height_attr:
            self.height.set(dpx(int(height_attr) + 2, h_zoom))
        else:
            self.height.set(dpx(IFRAME_HEIGHT_PX + 2, h_zoom))

        self.node.frame.frame_height = \
            self.height.get() - dpx(2, self.zoom.get())
        self.node.frame.frame_width = \
            self.width.get() - dpx(2, self.zoom.get())
        self.node.frame.document.width.mark()

        height = self.height.read(notify=self.ascent)
        self.ascent.set(-height)
        self.descent.set(0)

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
        self.height = ProtectedField(self, 'height')
        self.width = ProtectedField(self, 'width')
        self.x = ProtectedField(self, 'x')
        self.y = ProtectedField(self, 'y')
        self.zoom = ProtectedField(self, 'zoom')
        self.ascent = ProtectedField(self, 'ascent')
        self.descent = ProtectedField(self, 'descent')

    def should_paint(self):
        return True

    def layout(self) -> None:
        self.zoom.copy(self.parent.zoom)
        self.width.copy(self.parent.width)

        self.x.copy(self.parent.x)
        if self.previous:
            prev_y = self.previous.y.read(notify=self.y)
            prev_height = self.previous.height.read(notify=self.y)
            self.y.set(prev_y + prev_height)
        else:
            self.y.copy(self.parent.y)

        if not self.children:
            self.ascent.set(0)
            self.descent.set(0)
            self.height.set(0)
            return

        for word in self.children:
            word.layout()

        self.ascent.set(max([
            -child.ascent.read(notify=self.ascent)
            for child in self.children
        ]))

        self.descent.set(max([
            child.descent.read(notify=self.descent)
            for child in self.children
        ]))

        for child in self.children:
            new_y = self.y.read(notify=child.y)
            new_y += self.ascent.read(notify=child.y)
            if isinstance(child, TextLayout):
                new_y += child.ascent.read(notify=child.y) / 1.25
            else:
                new_y += child.ascent.read(notify=child.y)
            child.y.set(new_y)

        max_ascent = self.ascent.read(notify=self.height)
        max_descent = self.descent.read(notify=self.height)

        self.height.set(max_ascent + max_descent)

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
        self.children = ProtectedField(self, 'children', self.parent)
        self.zoom = ProtectedField(self, 'zoom', self.parent)
        self.node.layout_object = self
        self.frame = frame

        self.cursor_x = 0.0
        self.x = ProtectedField(self, 'x', self.parent)
        self.y = ProtectedField(self, 'y', self.parent)
        self.width = ProtectedField(self, 'width', self.parent)
        self.height = ProtectedField(self, 'height', self.parent)
        self.has_dirty_descendants = False

    def layout_needed(self):
        if self.zoom.dirty:
            return True
        if self.width.dirty:
            return True
        if self.height.dirty:
            return True
        if self.x.dirty:
            return True
        if self.y.dirty:
            return True
        if self.children.dirty:
            return True
        if self.has_dirty_descendants:
            return True
        return False

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
        if not self.layout_needed():
            return

        self.zoom.copy(self.parent.zoom)
        self.width.copy(self.parent.width)
        self.x.copy(self.parent.x)

        if self.previous:
            prev_y = self.previous.y.read(notify=self.y)
            prev_height = self.previous.height.read(notify=self.y)
            self.y.set(prev_y + prev_height)
        else:
            self.y.copy(self.parent.y)

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
            if self.children.dirty:
                self.temp_children = []
                self.new_line()
                self.recurse(self.node)
                self.children.set(self.temp_children)
                self.temp_children = None

        for child in self.children.get():
            child.layout()

        children = self.children.read(notify=self.height)
        new_height = sum([
            child.height.read(notify=self.height)
            for child in children
        ])
        self.height.set(new_height)
        self.has_dirty_descendants = False

    def add_inline_child(self, node: Node, w: float, child_class, frame: 'Frame', word=None):
        zoom = self.zoom.read(notify=self.children)
        width = self.width.read(notify=self.children)
        style = node.style.read(notify=self.children)
        if self.cursor_x + w > self.x + width:
            self.new_line()
        line = cast(LineLayout, self.temp_children[-1])
        previous_word = line.children[-1] if line.children else None
        if word:
            child = child_class(node, word, line, previous_word)
        else:
            child = child_class(node, line, previous_word, frame)
        line.children.append(child)
        self.cursor_x += w + \
            font(style, zoom).measureText(" ")

    def new_line(self):
        self.cursor_x = self.x
        last_line = self.temp_children[-1] if self.temp_children else None
        new_line = LineLayout(self.node, self, last_line)
        self.temp_children.append(new_line)

    def word(self, node: Text, word: str):
        zoom = self.zoom.read(notify=self.children)
        node_font = font(node.style, zoom, notify=self.children)
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
        self.x = ProtectedField(self, 'x')
        self.y = ProtectedField(self, 'y')
        self.width = ProtectedField(self, 'width')
        self.height = ProtectedField(self, 'height')
        self.zoom = ProtectedField(self, 'zoom')
        self.node.layout_object = self

    def layout(self, width: float, zoom: float):
        self.zoom.set(zoom)
        self.width.set(width - 2 * dpx(H_STEP, zoom))

        if not self.children:
            child = BlockLayout(self.node, self, None, self.frame)
        else:
            child = self.children[0]
        self.children = [child]

        self.x.set(dpx(H_STEP, zoom))
        self.y.set(dpx(V_STEP, zoom))
        child.layout()
        self.height.copy(child.height)

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
