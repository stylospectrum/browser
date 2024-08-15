import math
import threading
import urllib.parse
import sdl2  # type: ignore
import skia  # type: ignore

from typing import Union, cast

from css_parser import CSSParser, style
from html_parser import HTMLParser
from js_engine import JSContext
from node import Element, Text, Node
from utils import cascade_priority, tree_to_list, get_font, linespace, print_tree
from constants import SCROLL_STEP, V_STEP, WIDTH, HEIGHT, DEFAULT_URL, REFRESH_RATE_SEC
from url import URL
from layout import DocumentLayout, Layout
from draw_command import DrawLine, DrawText, DrawOutline, DrawRect, DrawCommand
from task import TaskRunner, Task

DEFAULT_STYLE_SHEET = CSSParser(open("src/default/browser.css").read()).parse()


def paint_tree(layout_object: Layout, display_list: list[DrawCommand]):
    cmds: list[DrawCommand] = []
    if layout_object.should_paint():
        cmds = layout_object.paint()
    for child in layout_object.children:
        paint_tree(child, cmds)

    if layout_object.should_paint():
        cmds = layout_object.paint_effects(cmds)
    display_list.extend(cmds)


class Tab:
    def __init__(self, tab_height: int):
        self.scroll = 0
        self.display_list: list[DrawCommand] = []
        self.url: Union[URL, None] = None
        self.tab_height = tab_height
        self.history: list[URL] = []
        self.focus: Union[Element, None] = None
        self.task_runner = TaskRunner(self)
        self.js: Union[JSContext, None] = None
        self.needs_render = False

    def set_needs_render(self):
        self.needs_render = True

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
        objs: list[Layout] = [obj for obj in tree_to_list(self.document, [])
                              if obj.x <= x < obj.x + obj.width
                              and obj.y <= y < obj.y + obj.height]

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

    def raster(self, canvas):
        for cmd in self.display_list:
            cmd.execute(canvas)

    def allowed_request(self, url: URL):
        return self.allowed_origins == None or \
            url.origin() in self.allowed_origins

    def load(self, url: URL, payload=None):
        headers, body = url.request(self.url, payload)
        self.history.append(url)
        self.url = url
        self.display_list = []
        self.rules = DEFAULT_STYLE_SHEET.copy()
        self.nodes = HTMLParser(body).parse()
        if self.js: self.js.discarded = True
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
        if not self.needs_render: return
        style(self.nodes, sorted(self.rules, key=cascade_priority))
        self.document = DocumentLayout(self.nodes)
        self.document.layout()
        self.display_list = []
        paint_tree(self.document, self.display_list)
        self.needs_render = False

    def scroll_down(self):
        max_y = max(
            self.document.height + 2 * V_STEP - self.tab_height, 0)
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)

    def go_back(self):
        if len(self.history) > 1:
            self.history.pop()
            back = self.history.pop()
            self.load(back)

    def __repr__(self):
        return "Tab(history={})".format(self.history)


class Chrome:
    def __init__(self, browser: 'Browser'):
        self.browser = browser
        self.font = get_font(20, "normal", "roman")
        self.font_height = linespace(self.font)

        self.padding = 5
        self.tab_bar_top = 0
        self.tab_bar_bottom = self.font_height + 2 * self.padding
        plus_width = self.font.measureText("+") + 2 * self.padding
        self.new_tab_rect = skia.Rect.MakeLTRB(
            self.padding, self.padding,
            self.padding + plus_width,
            self.padding + self.font_height)
        self.bottom = self.tab_bar_bottom

        self.url_bar_top = self.tab_bar_bottom
        self.url_bar_bottom = self.url_bar_top + \
            self.font_height + 2 * self.padding
        self.bottom = self.url_bar_bottom

        back_width = self.font.measureText("<") + 2 * self.padding
        self.back_rect = skia.Rect.MakeLTRB(
            self.padding,
            self.url_bar_top + self.padding,
            self.padding + back_width,
            self.url_bar_bottom - self.padding)

        self.address_rect = skia.Rect.MakeLTRB(
            self.back_rect.top() + self.padding,
            self.url_bar_top + self.padding,
            WIDTH - self.padding,
            self.url_bar_bottom - self.padding)

        self.focus = ""
        self.address_bar = ""

    def tab_rect(self, i: int):
        tabs_start = self.new_tab_rect.right() + self.padding
        tab_width = self.font.measureText("Tab X") + 2 * self.padding
        return skia.Rect.MakeLTRB(
            tabs_start + tab_width * i, self.tab_bar_top,
            tabs_start + tab_width * (i + 1), self.tab_bar_bottom)

    def paint(self):
        cmds: list[DrawCommand] = []

        cmds.append(DrawRect(
            skia.Rect.MakeLTRB(0, 0, WIDTH, self.bottom),
            "white"))
        cmds.append(DrawLine(
            0, self.bottom, WIDTH,
            self.bottom, "black", 1))

        cmds.append(DrawOutline(self.new_tab_rect, "black", 1))
        cmds.append(DrawText(
            self.new_tab_rect.left() + self.padding,
            self.new_tab_rect.top(),
            "+", self.font, "black"))

        for i, tab in enumerate(self.browser.tabs):
            bounds = self.tab_rect(i)
            cmds.append(DrawLine(
                bounds.left(), 0, bounds.left(), bounds.bottom(),
                "black", 1))
            cmds.append(DrawLine(
                bounds.right(), 0, bounds.right(), bounds.bottom(),
                "black", 1))
            cmds.append(DrawText(
                bounds.left() + self.padding, bounds.top() + self.padding,
                "Tab {}".format(i), self.font, "black"))

            if tab == self.browser.active_tab:
                cmds.append(DrawLine(
                    0, bounds.bottom(), bounds.left(), bounds.bottom(),
                    "black", 1))
                cmds.append(DrawLine(
                    bounds.right(), bounds.bottom(), WIDTH, bounds.bottom(),
                    "black", 1))

        cmds.append(DrawOutline(self.back_rect, "black", 1))
        cmds.append(DrawText(
            self.back_rect.left() + self.padding,
            self.back_rect.top(),
            "<", self.font, "black"))

        cmds.append(DrawOutline(self.address_rect, "black", 1))
        if self.focus == "address bar":
            cmds.append(DrawText(
                self.address_rect.left() + self.padding,
                self.address_rect.top(),
                self.address_bar, self.font, "black"))
            w = self.font.measureText(self.address_bar)
            cmds.append(DrawLine(
                self.address_rect.left() + self.padding + w,
                self.address_rect.top(),
                self.address_rect.left() + self.padding + w,
                self.address_rect.bottom(),
                "red", 1))
        else:
            url = str(self.browser.active_tab.url)
            cmds.append(DrawText(
                self.address_rect.left() + self.padding,
                self.address_rect.top(),
                url, self.font, "black"))

        return cmds

    def click(self, x: int, y: int):
        self.focus = ""

        if self.new_tab_rect.contains(x, y):
            self.browser.new_tab(URL(DEFAULT_URL))
        elif self.back_rect.contains(x, y):
            self.browser.active_tab.go_back()
            self.browser.raster_chrome()
            self.browser.raster_tab()
            self.browser.draw()
        elif self.address_rect.contains(x, y):
            self.focus = "address bar"
            self.address_bar = ""
        else:
            for i, tab in enumerate(self.browser.tabs):
                if self.tab_rect(i).contains(x, y):
                    self.browser.active_tab = tab
                    break

    def keypress(self, char: str):
        if self.focus == "address bar":
            self.address_bar += char
            return True
        return False

    def enter(self):
        if self.focus == "address bar":
            self.browser.active_tab.load(URL(self.address_bar))
            self.focus = None
            return True
        return False

    def blur(self):
        self.focus = None


class Browser:
    def __init__(self):
        self.tabs: list[Tab] = []
        self.active_tab: Tab = None
        self.sdl_window = sdl2.SDL_CreateWindow(b"Browser",
                                                sdl2.SDL_WINDOWPOS_CENTERED, sdl2.SDL_WINDOWPOS_CENTERED,
                                                WIDTH, HEIGHT, sdl2.SDL_WINDOW_SHOWN)
        self.root_surface = skia.Surface.MakeRaster(
            skia.ImageInfo.Make(
                WIDTH, HEIGHT,
                ct=skia.kRGBA_8888_ColorType,
                at=skia.kUnpremul_AlphaType))

        if sdl2.SDL_BYTEORDER == sdl2.SDL_BIG_ENDIAN:
            self.RED_MASK = 0xff000000
            self.GREEN_MASK = 0x00ff0000
            self.BLUE_MASK = 0x0000ff00
            self.ALPHA_MASK = 0x000000ff
        else:
            self.RED_MASK = 0x000000ff
            self.GREEN_MASK = 0x0000ff00
            self.BLUE_MASK = 0x00ff0000
            self.ALPHA_MASK = 0xff000000

        self.chrome = Chrome(self)
        self.focus = None

        self.chrome_surface = skia.Surface(
            WIDTH, math.ceil(self.chrome.bottom))
        self.tab_surface = None
        self.needs_raster_and_draw = False

    def set_needs_raster_and_draw(self):
        self.needs_raster_and_draw = True

    def schedule_animation_frame(self):
        def callback():
            active_tab = self.active_tab
            task = Task(active_tab.set_needs_render)
            active_tab.task_runner.schedule_task(task)
        threading.Timer(REFRESH_RATE_SEC, callback).start()

    def raster_and_draw(self):
        if not self.needs_raster_and_draw:
            return
        
        self.raster_chrome()
        self.raster_tab()
        self.draw()
        self.needs_raster_and_draw = False

    def new_tab(self, url: URL):
        new_tab = Tab(HEIGHT - self.chrome.bottom)
        new_tab.load(url)
        self.active_tab = new_tab
        self.tabs.append(new_tab)
        self.raster_chrome()
        self.raster_tab()
        self.draw()

    def handle_key(self, char: str):
        if not (0x20 <= ord(char) < 0x7f):
            return
        if self.chrome.focus:
            self.chrome.keypress(char)
            self.set_needs_raster_and_draw()
        elif self.focus == "content":
            self.active_tab.keypress(char)
            self.raster_tab()
            self.draw()

    def handle_down(self):
        self.active_tab.scroll_down()
        self.draw()

    def handle_click(self, e):
        if e.y < self.chrome.bottom:
            self.focus = None
            self.chrome.click(e.x, e.y)
            self.set_needs_raster_and_draw()
        else:
            if self.focus != "content":
                self.focus = "content"
                self.chrome.blur()
                self.set_needs_raster_and_draw()
            url = self.active_tab.url
            tab_y = e.y - self.chrome.bottom
            self.active_tab.click(e.x, tab_y)
            if self.active_tab.url != url:
                self.set_needs_raster_and_draw()
            self.raster_tab()

    def handle_enter(self):
        if self.chrome.enter():
            self.set_needs_raster_and_draw()

    def handle_quit(self):
        sdl2.SDL_DestroyWindow(self.sdl_window)

    def raster_tab(self):
        tab_height = math.ceil(
            self.active_tab.document.height + 2*V_STEP)

        if not self.tab_surface or \
                tab_height != self.tab_surface.height():
            self.tab_surface = skia.Surface(WIDTH, tab_height)

        canvas = self.tab_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)
        self.active_tab.raster(canvas)

    def raster_chrome(self):
        canvas = self.chrome_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        for cmd in self.chrome.paint():
            cmd.execute(canvas)

    def draw(self):
        canvas = self.root_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        tab_rect = skia.Rect.MakeLTRB(
            0, self.chrome.bottom, WIDTH, HEIGHT)
        tab_offset = self.chrome.bottom - self.active_tab.scroll
        canvas.save()
        canvas.clipRect(tab_rect)
        canvas.translate(0, tab_offset)
        self.tab_surface.draw(canvas, 0, 0)
        canvas.restore()

        chrome_rect = skia.Rect.MakeLTRB(
            0, 0, WIDTH, self.chrome.bottom)
        canvas.save()
        canvas.clipRect(chrome_rect)
        self.chrome_surface.draw(canvas, 0, 0)
        canvas.restore()

        skia_image = self.root_surface.makeImageSnapshot()
        skia_bytes = skia_image.tobytes()

        depth = 32  # Bits per pixel
        pitch = 4 * WIDTH  # Bytes per row
        sdl_surface = sdl2.SDL_CreateRGBSurfaceFrom(
            skia_bytes, WIDTH, HEIGHT, depth, pitch,
            self.RED_MASK, self.GREEN_MASK,
            self.BLUE_MASK, self.ALPHA_MASK)

        rect = sdl2.SDL_Rect(0, 0, WIDTH, HEIGHT)
        window_surface = sdl2.SDL_GetWindowSurface(self.sdl_window)
        # SDL_BlitSurface is what actually does the copy.
        sdl2.SDL_BlitSurface(sdl_surface, rect, window_surface, rect)
        sdl2.SDL_UpdateWindowSurface(self.sdl_window)
