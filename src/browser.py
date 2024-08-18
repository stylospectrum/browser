import math
import threading
import OpenGL.GL  # type: ignore
import urllib.parse
import sdl2  # type: ignore
import skia  # type: ignore

from typing import Union, cast

from css_parser import CSSParser, style
from html_parser import HTMLParser
from js_engine import JSContext
from node import Element, Text, Node
from utils import cascade_priority, tree_to_list, get_font, linespace, print_tree, add_parent_pointers
from constants import SCROLL_STEP, V_STEP, WIDTH, HEIGHT, DEFAULT_URL, REFRESH_RATE_SEC
from url import URL
from layout import DocumentLayout, Layout
from draw_command import DrawLine, DrawText, DrawOutline, DrawRect, PaintCommand
from composite import CompositedLayer, DrawCompositedLayer
from task import TaskRunner, Task
from measure import MeasureTime

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
    def __init__(self, url: URL, scroll: int, height: int, display_list: list[PaintCommand]):
        self.url = url
        self.scroll = scroll
        self.height = height
        self.display_list = display_list


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
                    self.set_needs_layout()

        self.render()

        scroll = None
        if self.scroll_changed_in_tab:
            scroll = self.scroll

        document_height = math.ceil(self.document.height + 2*V_STEP)
        commit_data = CommitData(
            cast(URL, self.url), scroll, document_height, self.display_list)
        self.display_list = []
        self.browser.commit(self, commit_data)
        self.scroll_changed_in_tab = False

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
        cmds: list[PaintCommand] = []

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
            url = str(self.browser.active_tab_url) if \
                self.browser.active_tab_url else ""
            cmds.append(DrawText(
                self.address_rect.left() + self.padding,
                self.address_rect.top(),
                url, self.font, "black"))

        return cmds

    def click(self, x: int, y: int):
        self.focus = ""

        if self.new_tab_rect.contains(x, y):
            self.browser.new_tab_internal(URL(DEFAULT_URL))
        elif self.back_rect.contains(x, y):
            task = Task(self.browser.active_tab.go_back)
            self.browser.active_tab.task_runner.schedule_task(task)
        elif self.address_rect.contains(x, y):
            self.focus = "address bar"
            self.address_bar = ""
        else:
            for i, tab in enumerate(self.browser.tabs):
                if self.tab_rect(i).contains(x, y):
                    self.browser.set_active_tab(tab)
                    active_tab = self.browser.active_tab
                    task = Task(active_tab.set_needs_render)
                    active_tab.task_runner.schedule_task(task)
                    break

    def keypress(self, char: str):
        if self.focus == "address bar":
            self.address_bar += char
            return True
        return False

    def enter(self):
        if self.focus == "address bar":
            self.browser.schedule_load(URL(self.address_bar))
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
                                                sdl2.SDL_WINDOWPOS_CENTERED,
                                                sdl2.SDL_WINDOWPOS_CENTERED,
                                                WIDTH, HEIGHT,
                                                sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_OPENGL)
        self.gl_context = sdl2.SDL_GL_CreateContext(
            self.sdl_window)
        print(("OpenGL initialized: vendor={}," +
               "renderer={}").format(
            OpenGL.GL.glGetString(OpenGL.GL.GL_VENDOR),
            OpenGL.GL.glGetString(OpenGL.GL.GL_RENDERER)))

        self.skia_context = skia.GrDirectContext.MakeGL()

        self.root_surface = \
            skia.Surface.MakeFromBackendRenderTarget(
                self.skia_context,
                skia.GrBackendRenderTarget(
                    WIDTH, HEIGHT, 0, 0,
                    skia.GrGLFramebufferInfo(
                        0, OpenGL.GL.GL_RGBA8)),
                skia.kBottomLeft_GrSurfaceOrigin,
                skia.kRGBA_8888_ColorType,
                skia.ColorSpace.MakeSRGB())
        assert self.root_surface is not None

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

        self.lock = threading.Lock()
        self.chrome = Chrome(self)
        self.measure = MeasureTime()

        self.chrome_surface = skia.Surface.MakeRenderTarget(
            self.skia_context, skia.Budgeted.kNo,
            skia.ImageInfo.MakeN32Premul(
                WIDTH, math.ceil(self.chrome.bottom)))
        assert self.chrome_surface is not None

        self.focus = None
        self.needs_raster_and_draw = False
        self.animation_timer = None
        self.needs_animation_frame = True
        self.active_tab_url = None
        self.active_tab_scroll = 0
        self.active_tab_height = 0
        self.active_tab_display_list = None
        self.composited_layers: list[CompositedLayer] = []
        self.draw_list: list[DrawCompositedLayer] = []

        threading.current_thread().name = "Browser thread"

    def set_active_tab(self, tab: Tab):
        self.active_tab = tab
        self.active_tab_scroll = 0
        self.active_tab_url = None
        self.needs_animation_frame = True
        self.animation_timer = None

    def clamp_scroll(self, scroll: int):
        height = self.active_tab_height
        max_scroll = height - (HEIGHT - self.chrome.bottom)
        return max(0, min(scroll, max_scroll))

    def commit(self, tab: Tab, data: CommitData):
        self.lock.acquire(blocking=True)
        if tab == self.active_tab:
            self.active_tab_url = data.url
            if data.scroll != None:
                self.active_tab_scroll = data.scroll
            self.active_tab_height = data.height
            if data.display_list:
                self.active_tab_display_list = data.display_list
            self.animation_timer = None
            self.set_needs_raster_and_draw()
        self.lock.release()

    def set_needs_animation_frame(self, tab: 'Tab'):
        self.lock.acquire(blocking=True)
        if tab == self.active_tab:
            self.needs_animation_frame = True
        self.lock.release()

    def set_needs_raster_and_draw(self):
        self.needs_raster_and_draw = True

    def schedule_animation_frame(self):
        def callback():
            self.lock.acquire(blocking=True)
            scroll = self.active_tab_scroll
            self.needs_animation_frame = False
            task = Task(self.active_tab.run_animation_frame, scroll)
            self.active_tab.task_runner.schedule_task(task)
            self.lock.release()

        self.lock.acquire(blocking=True)
        if self.needs_animation_frame and not self.animation_timer:
            self.animation_timer = \
                threading.Timer(REFRESH_RATE_SEC, callback)
            self.animation_timer.start()
        self.lock.release()

    def schedule_load(self, url, body=None):
        self.active_tab.task_runner.clear_pending_tasks()
        task = Task(self.active_tab.load, url, body)
        self.active_tab.task_runner.schedule_task(task)

    def new_tab(self, url: URL):
        self.lock.acquire(blocking=True)
        self.new_tab_internal(url)
        self.lock.release()

    def new_tab_internal(self, url: URL):
        new_tab = Tab(self, HEIGHT - self.chrome.bottom)
        self.tabs.append(new_tab)
        self.set_active_tab(new_tab)
        self.schedule_load(url)

    def handle_key(self, char: str):
        self.lock.acquire(blocking=True)
        if not (0x20 <= ord(char) < 0x7f):
            return
        if self.chrome.keypress(char):
            self.set_needs_raster_and_draw()
        elif self.focus == "content":
            task = Task(self.active_tab.keypress, char)
            self.active_tab.task_runner.schedule_task(task)
        self.lock.release()

    def handle_down(self):
        self.lock.acquire(blocking=True)
        if not self.active_tab_height:
            self.lock.release()
            return
        self.active_tab_scroll = self.clamp_scroll(
            self.active_tab_scroll + SCROLL_STEP)
        self.set_needs_raster_and_draw()
        self.needs_animation_frame = True
        self.lock.release()

    def handle_click(self, e):
        self.lock.acquire(blocking=True)
        if e.y < self.chrome.bottom:
            self.focus = None
            self.chrome.click(e.x, e.y)
            self.set_needs_raster_and_draw()
        else:
            if self.focus != "content":
                self.focus = "content"
                self.chrome.blur()
                self.set_needs_raster_and_draw()
            tab_y = e.y - self.chrome.bottom
            task = Task(self.active_tab.click, e.x, tab_y)
            self.active_tab.task_runner.schedule_task(task)
        self.lock.release()

    def handle_enter(self):
        self.lock.acquire(blocking=True)
        if self.chrome.enter():
            self.set_needs_raster_and_draw()
        self.lock.release()

    def handle_quit(self):
        self.measure.finish()
        for tab in self.tabs:
            tab.task_runner.set_needs_quit()
        sdl2.SDL_GL_DeleteContext(self.gl_context)
        sdl2.SDL_DestroyWindow(self.sdl_window)

    def composite(self):
        add_parent_pointers(self.active_tab_display_list)
        self.composited_layers = []
        all_commands = []
        paint_commands: list[PaintCommand] = []

        for cmd in self.active_tab_display_list:
            all_commands = tree_to_list(cmd, all_commands)
        paint_commands = [cmd for cmd in all_commands
                          if isinstance(cmd, PaintCommand)]

        for cmd in paint_commands:
            layer = CompositedLayer(self.skia_context, cmd)
            self.composited_layers.append(layer)

    def paint_draw_list(self):
        new_effects = {}
        self.draw_list = []
        for composited_layer in self.composited_layers:
            current_effect = \
                DrawCompositedLayer(composited_layer)
            if not composited_layer.display_items:
                continue
            parent = composited_layer.display_items[0].parent

            while parent:
                if parent in new_effects:
                    new_parent = new_effects[parent]
                    new_parent.children.append(current_effect)
                    break
                else:
                    current_effect = \
                        parent.clone(current_effect)
                    new_effects[parent] = current_effect
                    parent = parent.parent

            if not parent:
                self.draw_list.append(current_effect)

    def raster_tab(self):
        for composited_layer in self.composited_layers:
            composited_layer.raster()

    def raster_chrome(self):
        canvas = self.chrome_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        for cmd in self.chrome.paint():
            cmd.execute(canvas)

    def draw(self):
        canvas = self.root_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        canvas.save()
        canvas.translate(0,
                         self.chrome.bottom - self.active_tab_scroll)
        for item in self.draw_list:
            item.execute(canvas)
        canvas.restore()

        chrome_rect = skia.Rect.MakeLTRB(
            0, 0, WIDTH, self.chrome.bottom)
        canvas.save()
        canvas.clipRect(chrome_rect)
        self.chrome_surface.draw(canvas, 0, 0)
        canvas.restore()

        self.root_surface.flushAndSubmit()
        sdl2.SDL_GL_SwapWindow(self.sdl_window)

    def composite_raster_and_draw(self):
        self.lock.acquire(blocking=True)
        if not self.needs_raster_and_draw:
            self.lock.release()
            return
        self.measure.time('raster/draw')
        self.composite()
        self.raster_chrome()
        self.raster_tab()
        self.paint_draw_list()
        self.draw()
        self.measure.stop('raster/draw')
        self.needs_raster_and_draw = False
        self.lock.release()
