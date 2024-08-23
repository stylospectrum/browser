import math
import urllib.parse
import skia

from typing import TYPE_CHECKING, Union, cast

from html_parser import HTMLParser
from css_parser import CSSParser, style
from draw_command import PaintCommand, Blend, VisualEffect
from a11y import AccessibilityNode
from layout import DocumentLayout
from url import URL
from node import Element, Text
from task import TaskRunner, Task
from js_engine import JSContext
from constants import V_STEP, INHERITED_PROPERTIES, SCROLL_STEP, BROKEN_IMAGE
from utils import tree_to_list, cascade_priority, absolute_bounds_for_obj, is_focusable, get_tabindex

if TYPE_CHECKING:
    from browser import Browser

DEFAULT_STYLE_SHEET = CSSParser(open("src/default/browser.css").read()).parse()


def paint_tree(layout_object, display_list: list[PaintCommand]):
    cmds: list[PaintCommand] = []
    if layout_object.should_paint():
        cmds = layout_object.paint()
    for child in layout_object.children:
        paint_tree(child, cmds)

    if layout_object.should_paint():
        cmds = layout_object.paint_effects(cmds)
    display_list.extend(cmds)


class CommitData:
    def __init__(self, url: URL, scroll: int, height: int, display_list: list[Union[VisualEffect, PaintCommand]], composited_updates: Union[None, dict[Element, Blend]], accessibility_tree: Union[AccessibilityNode, None], focus: Element):
        self.url = url
        self.scroll = scroll
        self.height = height
        self.display_list = display_list
        self.composited_updates = composited_updates
        self.accessibility_tree = accessibility_tree
        self.focus = focus


class Tab:
    def __init__(self, browser: 'Browser', tab_height: int):
        self.zoom: float = 1
        self.scroll: float = 0
        self.display_list: list[PaintCommand] = []
        self.url: Union[URL, None] = None
        self.tab_height = tab_height
        self.history: list[URL] = []
        self.focus: Union[Element, None] = None
        self.task_runner = TaskRunner(self)
        self.js: Union[JSContext, None] = None
        self.needs_style = False
        self.needs_layout = False
        self.needs_paint = False
        self.scroll_changed_in_tab = False
        self.needs_focus_scroll = False
        self.browser = browser
        self.dark_mode: bool = browser.dark_mode
        self.needs_accessibility = False
        self.accessibility_tree: Union[AccessibilityNode, None] = None
        self.composited_updates: list[Element] = []
        self.root_frame = None
        self.task_runner.start_thread()

    def advance_tab(self):
        focusable_nodes = [node
                           for node in tree_to_list(self.nodes, [])
                           if isinstance(node, Element) and is_focusable(node)]
        focusable_nodes.sort(key=get_tabindex)

        if self.focus in focusable_nodes:
            idx = focusable_nodes.index(self.focus) + 1
        else:
            idx = 0

        if idx < len(focusable_nodes):
            self.focus_element(focusable_nodes[idx])
            self.browser.focus_content()
        else:
            self.focus_element(None)
            self.browser.focus_addressbar()
        self.set_needs_render()

    def set_dark_mode(self, val: bool):
        self.dark_mode = val
        self.set_needs_render()

    def zoom_by(self, increment: bool):
        if increment:
            self.zoom *= 1.1
            self.scroll *= 1.1
        else:
            self.zoom *= 1/1.1
            self.scroll *= 1/1.1
        self.scroll_changed_in_tab = True
        self.set_needs_render()

    def reset_zoom(self):
        self.scroll /= self.zoom
        self.zoom = 1
        self.scroll_changed_in_tab = True
        self.set_needs_render()

    def scroll_to(self, elt: Element):
        objs = [
            obj for obj in tree_to_list(self.document, [])
            if obj.node == self.focus
        ]
        if not objs:
            return
        obj = objs[0]

        if self.scroll < obj.y < self.scroll + self.tab_height:
            return

        document_height = math.ceil(self.document.height + 2*V_STEP)
        new_scroll = obj.y - SCROLL_STEP
        self.scroll = self.clamp_scroll(new_scroll)
        self.scroll_changed_in_tab = True

    def clamp_scroll(self, scroll):
        height = math.ceil(self.document.height + 2*V_STEP)
        max_scroll = height - self.tab_height
        return max(0, min(scroll, max_scroll))

    def run_animation_frame(self, scroll):
        if not self.scroll_changed_in_tab:
            self.scroll = scroll

        self.browser.measure.time('script-runRAFHandlers')
        cast(JSContext, self.js).interp.evaljs("__runRAFHandlers()")
        self.browser.measure.stop('script-runRAFHandlers')

        for node in tree_to_list(self.nodes, []):
            for (property_name, animation) in \
                    node.animations.items():
                value = animation.animate()
                if value:
                    node.style[property_name] = value
                    self.composited_updates.append(node)
                    self.set_needs_paint()

                if animation.frame_count + 1 >= animation.num_frames:
                    self.browser.needs_animation_frame = False

        needs_composite = self.needs_style or self.needs_layout
        self.render()

        if self.needs_focus_scroll and self.focus:
            self.scroll_to(self.focus)
        self.needs_focus_scroll = False

        composited_updates = None
        if not needs_composite:
            composited_updates = {}
            for node in self.composited_updates:
                composited_updates[node] = node.blend_op
        self.composited_updates = []

        scroll = None
        if self.scroll_changed_in_tab:
            scroll = self.scroll

        document_height = math.ceil(self.document.height + 2*V_STEP)
        commit_data = CommitData(
            cast(URL, self.url), scroll, document_height, self.display_list, composited_updates, self.accessibility_tree, self.focus)
        self.display_list = None
        self.accessibility_tree = None
        self.scroll_changed_in_tab = False
        self.browser.commit(self, commit_data)

    def set_needs_render(self):
        self.needs_style = True
        self.browser.set_needs_animation_frame(self)

    def set_needs_layout(self):
        self.needs_layout = True
        self.browser.set_needs_animation_frame(self)

    def set_needs_paint(self):
        self.needs_paint = True
        self.browser.set_needs_animation_frame(self)

    def submit_form(self, elt: Element):
        if self.js and self.js.dispatch_event("submit", elt):
            return

        inputs = [node for node in tree_to_list(elt, [])
                  if isinstance(node, Element)
                  and node.tag == "input"
                  and "name" in node.attributes]

        body = ""
        for input in inputs:
            name = input.attributes["name"]
            value = input.attributes.get("value", "")
            name = urllib.parse.quote(name)
            value = urllib.parse.quote(value)
            body += "&" + name + "=" + value
        body = body[1:]

        url = cast(URL, self.url).resolve(
            cast(Element, elt).attributes["action"])
        self.load(url, body)

    def activate_element(self, elt: Element):
        if elt.tag == "input":
            elt.attributes["value"] = ""
            self.set_needs_render()
        elif elt.tag == "a" and "href" in elt.attributes:
            url = cast(URL, self.url).resolve(elt.attributes["href"])
            self.load(url)
        elif elt.tag == "button":
            while elt:
                if elt.tag == "form" and "action" in elt.attributes:
                    self.submit_form(elt)
                elt = cast(Element, elt.parent)

    def focus_element(self, node: Union[Element, None]):
        if node and node != self.focus:
            self.needs_focus_scroll = True
        if self.focus:
            self.focus.is_focused = False
        self.focus = node
        if node:
            node.is_focused = True
        self.set_needs_render()

    def enter(self):
        if not self.focus:
            return
        if self.js and self.js.dispatch_event("click", self.focus):
            return
        self.activate_element(self.focus)

    def keypress(self, char: str):
        if self.focus and self.focus.tag == "input":
            if not "value" in self.focus.attributes:
                self.activate_element(self.focus)
            if self.js and self.js.dispatch_event("keydown", self.focus):
                return
            self.focus.attributes["value"] += char
            self.set_needs_render()

    def click(self, x: float, y: float):
        self.render()
        self.focus_element(None)
        y += self.scroll
        loc_rect = skia.Rect.MakeXYWH(x, y, 1, 1)
        objs = [obj for obj in tree_to_list(self.document, [])
                if absolute_bounds_for_obj(obj).intersects(
                    loc_rect)]
        if not objs:
            return
        elt = objs[-1].node
        if elt and self.js and self.js.dispatch_event("click", elt):
            return
        while elt:
            if isinstance(elt, Text):
                pass
            elif is_focusable(elt):
                self.focus_element(elt)
                self.activate_element(elt)
                return
            elt = elt.parent

    def allowed_request(self, url: URL):
        return self.allowed_origins == None or \
            url.origin() in self.allowed_origins

    def load(self, url: URL, payload=None):
        self.history.append(url)
        self.task_runner.clear_pending_tasks()
        self.root_frame = Frame(self, None, None)
        self.root_frame.load(url, payload)
        self.root_frame.frame_width = WIDTH
        self.root_frame.frame_height = self.tab_height
        
        self.focus = None
        self.scroll = 0
        self.zoom = 1
        self.scroll_changed_in_tab = True
        headers, body = url.request(self.url, payload)
        body = body.decode("utf8", "replace")
        self.history.append(url)
        self.url = url
        self.display_list = []
        self.rules = DEFAULT_STYLE_SHEET.copy()
        self.nodes = HTMLParser(body).parse()
        if self.js:
            self.js.discarded = True
        self.js = JSContext(self)
        self.allowed_origins = None

        if "content-security-policy" in headers:
            csp = headers["content-security-policy"].split()
            if len(csp) > 0 and csp[0] == "default-src":
                self.allowed_origins = []
                for origin in csp[1:]:
                    self.allowed_origins.append(URL(origin).origin())

        links = [node.attributes["href"]
                 for node in tree_to_list(self.nodes, [])
                 if isinstance(node, Element)
                 and node.tag == "link"
                 and node.attributes.get("rel") == "stylesheet"
                 and "href" in node.attributes]

        scripts = [node.attributes["src"] for node
                   in tree_to_list(self.nodes, [])
                   if isinstance(node, Element)
                   and node.tag == "script"
                   and "src" in node.attributes]

        for script in scripts:
            script_url = url.resolve(script)
            if not self.allowed_request(script_url):
                print("Blocked script", script, "due to CSP")
                continue
            try:
                headers, body = script_url.request(url)
                body = body.decode("utf8", "replace")
            except:
                continue
            task = Task(self.js.run, script_url, body)
            self.task_runner.schedule_task(task)

        for link in links:
            style_url = url.resolve(link)
            if not self.allowed_request(style_url):
                print("Blocked style", link, "due to CSP")
                continue
            try:
                headers, body = style_url.request(url)
                body = body.decode("utf8", "replace")
            except:
                continue
            self.rules.extend(CSSParser(body).parse())


        images = [node
            for node in tree_to_list(self.nodes, [])
            if isinstance(node, Element)
            and node.tag == "img"]
        for img in images:
            try:
                src = img.attributes.get("src", "")
                image_url = url.resolve(src)
                assert self.allowed_request(image_url), \
                    "Blocked load of " + str(image_url) + " due to CSP"
                header, body = image_url.request(url)

                img.encoded_data = body
                data = skia.Data.MakeWithoutCopy(body)
                img.image = skia.Image.MakeFromEncoded(data)
                assert img.image, \
                    "Failed to recognize image format for " + str(image_url)
            except Exception as e:
                print("Image", img.attributes.get("src", ""),
                    "crashed", e)
                img.image = BROKEN_IMAGE
        self.set_needs_render()

    def render(self):
        self.browser.measure.time('render')

        if self.needs_style:
            if self.dark_mode:
                INHERITED_PROPERTIES["color"] = "white"
            else:
                INHERITED_PROPERTIES["color"] = "black"
            style(self.nodes, sorted(self.rules, key=cascade_priority), self)
            self.needs_layout = True
            self.needs_style = False

        if self.needs_layout:
            self.document = DocumentLayout(self.nodes)
            self.document.layout(self.zoom)
            self.needs_paint = True
            self.needs_accessibility = True
            self.needs_layout = False

        if self.needs_accessibility:
            self.accessibility_tree = AccessibilityNode(self.nodes)
            self.accessibility_tree.build()
            self.needs_accessibility = False

        if self.needs_paint:
            self.display_list = []
            paint_tree(self.document, self.display_list)
            self.needs_paint = False

        clamped_scroll = self.clamp_scroll(self.scroll)
        if clamped_scroll != self.scroll:
            self.scroll_changed_in_tab = True
        self.scroll = clamped_scroll

        self.browser.measure.stop('render')

    def go_back(self):
        if len(self.history) > 1:
            self.history.pop()
            back = self.history.pop()
            self.load(back)

    def __repr__(self):
        return "Tab(history={})".format(self.history)
