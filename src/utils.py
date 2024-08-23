import skia

from typing import Union, TypeVar, Any, cast, TYPE_CHECKING

from constants import NAMED_COLORS
from css_parser import CSSRule, parse_transform

if TYPE_CHECKING:
    from node import Element

T = TypeVar('T')
FONTS: dict[tuple[str, str], tuple] = {}


def parse_blend_mode(blend_mode_str: Union[str, None]):
    if blend_mode_str == "multiply":
        return skia.BlendMode.kMultiply
    elif blend_mode_str == "difference":
        return skia.BlendMode.kDifference
    elif blend_mode_str == "destination-in":
        return skia.BlendMode.kDstIn
    elif blend_mode_str == "source-over":
        return skia.BlendMode.kSrcOver
    else:
        return skia.BlendMode.kSrcOver


def get_font(size: int, weight: str, style: str):
    key = (weight, style)
    if key not in FONTS:
        if weight == "bold":
            skia_weight = skia.FontStyle.kBold_Weight
        else:
            skia_weight = skia.FontStyle.kNormal_Weight
        if style == "italic":
            skia_style = skia.FontStyle.kItalic_Slant
        else:
            skia_style = skia.FontStyle.kUpright_Slant
        skia_width = skia.FontStyle.kNormal_Width
        style_info = \
            skia.FontStyle(skia_weight, skia_width, skia_style)
        font = skia.Typeface('Arial', style_info)
        FONTS[key] = font
    return skia.Font(FONTS[key], size)


def cascade_priority(rule: CSSRule):
    media, selector, body = rule
    return selector.priority


def parse_color(color: str):
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return skia.Color(r, g, b)
    elif color.startswith("#") and len(color) == 9:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        a = int(color[7:9], 16)
        return skia.Color(r, g, b, a)
    elif color in NAMED_COLORS:
        return parse_color(NAMED_COLORS[color])
    else:
        return skia.ColorBLACK

def parse_outline(outline_str: Union[str, None]):
    if not outline_str: return None
    values = outline_str.split(" ")
    if len(values) != 3: return None
    if values[1] != "solid": return None
    return int(values[0][:-2]), values[2]


def linespace(font) -> int:
    metrics = font.getMetrics()
    return metrics.fDescent - metrics.fAscent


def tree_to_list(tree: T, list: list) -> list[T]:
    list.append(tree)
    for child in cast(Any, tree).children:
        tree_to_list(child, list)
    return list


def print_tree(node, indent=0):
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


def add_parent_pointers(nodes, parent=None):
    for node in nodes:
        node.parent = parent
        add_parent_pointers(node.children, node)


def map_translation(rect, translation, reversed=False):
    if not translation:
        return rect
    else:
        (x, y) = translation
        matrix = skia.Matrix()
        if reversed:
            matrix.setTranslate(-x, -y)
        else:
            matrix.setTranslate(x, y)
        return matrix.mapRect(rect)


def absolute_bounds_for_obj(obj):
    rect = skia.Rect.MakeXYWH(
        obj.x, obj.y, obj.width, obj.height)
    cur = obj.node
    while cur:
        rect = map_translation(rect,
                               parse_transform(
                                   cur.style.get("transform", "")))
        cur = cur.parent
    return rect


def local_to_absolute(display_item, rect):
    while display_item.parent:
        rect = display_item.parent.map(rect)
        display_item = display_item.parent
    return rect


def absolute_to_local(display_item, rect):
    parent_chain = []
    while display_item.parent:
        parent_chain.append(display_item.parent)
        display_item = display_item.parent
    for parent in reversed(parent_chain):
        rect = parent.unmap(rect)
    return rect


def dpx(css_px: float, zoom: float):
    return css_px * zoom

def is_focusable(node: 'Element'):
    if get_tabindex(node) < 0:
        return False
    elif "tabindex" in node.attributes:
        return True
    else:
        return node.tag in ["input", "button", "a"]

def get_tabindex(node: 'Element'):
    tabindex = int(node.attributes.get("tabindex", "9999999"))
    return 9999999 if tabindex == 0 else tabindex