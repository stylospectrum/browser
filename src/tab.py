import math
import urllib.parse
import skia

from typing import TYPE_CHECKING, Union, cast

from html_parser import HTMLParser
from css_parser import CSSParser, style
from draw_command import PaintCommand, Blend
from layout import Layout, DocumentLayout
from url import URL
from node import Element, Text, Node
from task import TaskRunner, Task
from js_engine import JSContext
from constants import V_STEP
from utils import tree_to_list, cascade_priority, absolute_bounds_for_obj

if TYPE_CHECKING:
    from browser import Browser

DEFAULT_STYLE_SHEET = CSSParser(open("src/default/browser.css").read()).parse()


def paint_tree(layout_object: Layout, display_list: list[PaintCommand]):
    cmds: list[PaintCommand] = []
    if layout_object.should_paint():
        cmds = layout_object.paint()
    for child in layout_object.children:
        paint_tree(child, cmds)

    if layout_object.should_paint():
        cmds = layout_object.paint_effects(cmds)
    display_list.extend(cmds)


class CommitData:
    def __init__(self, url: URL, scroll: int, height: int, display_list: list[PaintCommand], composited_updates: Union[None, dict[Element, Blend]]):
        self.url = url
        self.scroll = scroll
        self.height = height
        self.display_list = display_list
        self.composited_updates = composited_updates


class Tab:
    def __init__(self, browser: 'Browser', tab_height: int):
        self.scroll = 0
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
        self.browser = browser
        self.composited_updates: list[Element] = []
        self.task_runner.start_thread()

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
            cast(URL, self.url), scroll, document_height, self.display_list, composited_updates)
        self.display_list = None
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

    def keypress(self, char: str):
        if self.focus:
            if self.js and self.js.dispatch_event("keydown", self.focus):
                return
            self.focus.attributes["value"] += char
            self.set_needs_render()

    def click(self, x: int, y: int):
        self.render()
        self.focus = None
        y += self.scroll
        loc_rect = skia.Rect.MakeXYWH(x, y, 1, 1)
        objs = [obj for obj in tree_to_list(self.document, [])
                if absolute_bounds_for_obj(obj).intersects(
                    loc_rect)]

        if not objs:
            return
        elt: Union[Node, None] = objs[-1].node

        while elt:
            if isinstance(elt, Text):
                pass
            elif elt.tag == "a" and "href" in elt.attributes:
                if self.js and self.js.dispatch_event("click", elt):
                    return
                url = cast(URL, self.url).resolve(elt.attributes["href"])
                return self.load(url)
            elif elt.tag == "input":
                if self.js and self.js.dispatch_event("click", elt):
                    return
                elt.attributes["value"] = ""
                if self.focus:
                    self.focus.is_focused = False
                self.focus = elt
                elt.is_focused = True
                self.set_needs_render()
                return
            elif elt.tag == "button":
                if self.js and self.js.dispatch_event("click", elt):
                    return
                while elt:
                    if isinstance(elt, Text):
                        pass
                    elif elt.tag == "form" and "action" in elt.attributes:
                        return self.submit_form(elt)
                    elt = elt.parent

            if elt is not None:
                elt = elt.parent

    def allowed_request(self, url: URL):
        return self.allowed_origins == None or \
            url.origin() in self.allowed_origins

    def load(self, url: URL, payload=None):
        self.scroll = 0
        self.scroll_changed_in_tab = True
        headers, body = url.request(self.url, payload)
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
            except:
                continue
            self.rules.extend(CSSParser(body).parse())
        self.set_needs_render()

    def render(self):
        self.browser.measure.time('render')

        if self.needs_style:
            style(self.nodes, sorted(self.rules, key=cascade_priority), self)
            self.needs_layout = True
            self.needs_style = False

        if self.needs_layout:
            self.document = DocumentLayout(self.nodes)
            self.document.layout()
            self.needs_paint = True
            self.needs_layout = False

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
