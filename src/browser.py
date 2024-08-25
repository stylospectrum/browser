import math
import threading
import OpenGL.GL
import sdl2
import skia

from typing import Any, Union, cast

from utils import tree_to_list, add_parent_pointers, local_to_absolute, print_tree
from constants import SCROLL_STEP, WIDTH, HEIGHT, REFRESH_RATE_SEC, V_STEP
from url import URL
from draw_command import PaintCommand, DrawOutline
from composite import CompositedLayer, DrawCompositedLayer
from task import Task
from a11y import AccessibilityNode
from measure import MeasureTime
from tab import Tab, CommitData
from chrome import Chrome
from draw_command import Blend, VisualEffect
from screen_reader import ScreenReader
from node import Element


class Browser:
    def __init__(self) -> None:
        self.tabs: list[Tab] = []
        self.active_tab: Tab
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
        self.screen_reader = ScreenReader(self)

        self.chrome_surface = skia.Surface.MakeRenderTarget(
            self.skia_context, skia.Budgeted.kNo,
            skia.ImageInfo.MakeN32Premul(
                WIDTH, math.ceil(self.chrome.bottom)))
        assert self.chrome_surface is not None

        self.focus = None
        self.needs_composite = False
        self.needs_raster = False
        self.needs_draw = False
        self.needs_animation_frame = True
        self.animation_timer = None
        self.active_tab_url: Union[URL, None] = None
        self.active_tab_scroll = 0
        self.active_tab_height = 0
        self.active_tab_display_list: list[Union[VisualEffect, PaintCommand]] = [
        ]
        self.dark_mode = False
        self.needs_accessibility = False
        self.accessibility_is_on = False
        self.needs_speak_hovered_node = False
        self.tab_focus: Union[Element, None] = None
        self.last_tab_focus: Union[Element, None] = None
        self.composited_layers: list[CompositedLayer] = []
        self.draw_list: list[Union[VisualEffect, PaintCommand]] = []
        self.accessibility_tree: Union[AccessibilityNode, None] = None
        self.composited_updates: dict[Element, Blend] = {}
        self.active_alerts: list[AccessibilityNode] = []
        self.spoken_alerts: list[AccessibilityNode] = []
        self.pending_hover: Union[tuple[int, int], None] = None
        self.hovered_a11y_node: Union[AccessibilityNode, None] = None

        threading.current_thread().name = "Browser thread"

    def update_accessibility(self) -> None:
        if not self.accessibility_tree:
            return

        if not self.screen_reader.has_spoken_document:
            self.screen_reader.speak_document()
            self.screen_reader.has_spoken_document = True

        self.active_alerts = [
            node for node in tree_to_list(
                self.accessibility_tree, [])
            if node.role == "alert"
        ]

        for alert in self.active_alerts:
            if alert not in self.spoken_alerts:
                self.screen_reader.speak_node(alert, "New alert")
                self.spoken_alerts.append(alert)

        new_spoken_alerts: list[AccessibilityNode] = []
        for old_node in self.spoken_alerts:
            new_nodes = [
                node for node in tree_to_list(
                    self.accessibility_tree, [])
                if node.node == old_node.node
                and node.role == "alert"
            ]
            if new_nodes:
                new_spoken_alerts.append(new_nodes[0])
        self.spoken_alerts = new_spoken_alerts

        if self.tab_focus and \
                self.tab_focus != self.last_tab_focus:
            nodes = [node for node in tree_to_list(
                self.accessibility_tree, [])
                if node.node == self.tab_focus]
            if nodes:
                self.focus_a11y_node = nodes[0]
                self.screen_reader.speak_node(
                    self.focus_a11y_node, "element focused ")
            self.last_tab_focus = self.tab_focus

        if self.needs_speak_hovered_node and self.hovered_a11y_node:
            self.screen_reader.speak_node(self.hovered_a11y_node, "Hit test ")
        self.needs_speak_hovered_node = False

    def focus_addressbar(self):
        self.lock.acquire(blocking=True)
        self.chrome.focus_addressbar()
        self.set_needs_raster()
        self.lock.release()

    def focus_content(self):
        self.lock.acquire(blocking=True)
        self.chrome.blur()
        self.focus = "content"
        self.lock.release()

    def cycle_tabs(self):
        self.lock.acquire(blocking=True)
        active_idx = self.tabs.index(self.active_tab)
        new_active_idx = (active_idx + 1) % len(self.tabs)
        self.set_active_tab(self.tabs[new_active_idx])
        self.lock.release()

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        task = Task(self.active_tab.set_dark_mode, self.dark_mode)
        self.active_tab.task_runner.schedule_task(task)

    def clear_data(self):
        self.active_tab_scroll = 0
        self.active_tab_url = None
        self.accessibility_tree = None
        self.active_tab_display_list = []
        self.composited_layers = []
        self.composited_updates = {}

    def set_active_tab(self, tab):
        self.active_tab = tab
        task = Task(self.active_tab.set_dark_mode, self.dark_mode)
        self.active_tab.task_runner.schedule_task(task)
        task = Task(self.active_tab.set_needs_render_all_frames)
        self.active_tab.task_runner.schedule_task(task)

        self.clear_data()
        self.needs_animation_frame = True
        self.animation_timer = None

    def go_back(self):
        task = Task(self.active_tab.go_back)
        self.active_tab.task_runner.schedule_task(task)
        self.clear_data()

    def clamp_scroll(self, scroll: int):
        height = self.active_tab_height
        max_scroll = height - (HEIGHT - self.chrome.bottom - 2*V_STEP)
        return max(0, min(scroll, max_scroll))

    def commit(self, tab: Tab, data: CommitData):
        self.lock.acquire(blocking=True)
        if tab == self.active_tab:
            self.active_tab_url = data.url
            self.active_tab_height = data.height
            self.animation_timer = None
            self.accessibility_tree = data.accessibility_tree
            self.tab_focus = data.focus
            self.root_frame_focused = data.root_frame_focused

            if data.scroll != None:
                self.active_tab_scroll = data.scroll

            if data.display_list:
                self.active_tab_display_list = data.display_list

            if data.composited_updates == None:
                self.composited_updates = {}
                self.set_needs_composite()
            else:
                self.composited_updates = cast(
                    dict[Element, Blend], data.composited_updates)
                self.set_needs_draw()

        self.lock.release()

    def set_needs_animation_frame(self, tab: 'Tab'):
        self.lock.acquire(blocking=True)
        if tab == self.active_tab:
            self.needs_animation_frame = True
        self.lock.release()

    def set_needs_raster(self):
        self.needs_raster = True
        self.needs_draw = True

    def set_needs_composite(self):
        self.needs_composite = True
        self.needs_raster = True
        self.needs_draw = True

    def set_needs_draw(self):
        self.needs_draw = True

    def set_needs_accessibility(self):
        if not self.accessibility_is_on:
            return
        self.needs_accessibility = True
        self.needs_draw = True

    def toggle_accessibility(self):
        self.lock.acquire(blocking=True)
        self.accessibility_is_on = not self.accessibility_is_on
        self.set_needs_accessibility()
        self.lock.release()

    def get_latest(self, effect: VisualEffect):
        node = effect.node
        if node not in self.composited_updates:
            return effect
        if not isinstance(effect, Blend):
            return effect
        return self.composited_updates[node]

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

    def handle_tab(self):
        self.focus = "content"
        task = Task(self.active_tab.advance_tab)
        self.active_tab.task_runner.schedule_task(task)

    def handle_key(self, char: str):
        self.lock.acquire(blocking=True)
        if not (0x20 <= ord(char) < 0x7f):
            return
        if self.chrome.keypress(char):
            self.set_needs_raster()
        elif self.focus == "content":
            task = Task(self.active_tab.keypress, char)
            self.active_tab.task_runner.schedule_task(task)
        self.lock.release()

    def handle_down(self):
        self.lock.acquire(blocking=True)
        if self.root_frame_focused:
            if not self.active_tab_height:
                self.lock.release()
                return
            self.active_tab_scroll = \
                self.clamp_scroll(self.active_tab_scroll + SCROLL_STEP)
            self.set_needs_draw()
            self.needs_animation_frame = True
            self.lock.release()
            return
        task = Task(self.active_tab.scroll_down)
        self.active_tab.task_runner.schedule_task(task)
        self.needs_animation_frame = True
        self.lock.release()

    def handle_hover(self, event):
        if not self.accessibility_is_on or \
                not self.accessibility_tree:
            return
        self.pending_hover = (event.x, event.y - self.chrome.bottom)
        self.set_needs_accessibility()

    def handle_click(self, e):
        self.lock.acquire(blocking=True)
        if e.y < self.chrome.bottom:
            self.focus = None
            self.chrome.click(e.x, e.y)
            self.set_needs_raster()
        else:
            if self.focus != "content":
                self.set_needs_raster()
            self.focus = "content"
            self.chrome.blur()
            tab_y = e.y - self.chrome.bottom
            task = Task(self.active_tab.click, e.x, tab_y)
            self.active_tab.task_runner.schedule_task(task)
        self.lock.release()

    def handle_enter(self):
        self.lock.acquire(blocking=True)
        if self.chrome.enter():
            self.set_needs_raster()
        elif self.focus == "content":
            task = Task(self.active_tab.enter)
            self.active_tab.task_runner.schedule_task(task)
        self.lock.release()

    def increment_zoom(self, increment: bool):
        task = Task(self.active_tab.zoom_by, increment)
        self.active_tab.task_runner.schedule_task(task)

    def reset_zoom(self):
        task = Task(self.active_tab.reset_zoom)
        self.active_tab.task_runner.schedule_task(task)

    def handle_quit(self):
        self.measure.finish()
        for tab in self.tabs:
            tab.task_runner.set_needs_quit()
        sdl2.SDL_GL_DeleteContext(self.gl_context)
        sdl2.SDL_DestroyWindow(self.sdl_window)

    def composite(self) -> None:
        self.composited_layers = []
        add_parent_pointers(self.active_tab_display_list)
        all_commands: list[Union[VisualEffect, PaintCommand]] = []

        for cmd in self.active_tab_display_list:
            all_commands = tree_to_list(cmd, all_commands)

        non_composited_commands = [cmd
                                   for cmd in all_commands
                                   if isinstance(cmd, PaintCommand) or
                                   not cmd.needs_compositing
                                   if not cmd.parent or cmd.parent.needs_compositing
                                   ]

        for cmd in non_composited_commands:
            for layer in reversed(self.composited_layers):
                if layer.can_merge(cmd):
                    layer.add(cmd)
                    break
                elif skia.Rect.Intersects(
                        layer.absolute_bounds(),
                        local_to_absolute(cmd, cmd.rect)):
                    layer = CompositedLayer(self.skia_context, cmd)
                    self.composited_layers.append(layer)
                    break
            else:
                layer = CompositedLayer(self.skia_context, cmd)
                self.composited_layers.append(layer)

    def paint_draw_list(self) -> None:
        new_effects: dict[VisualEffect, VisualEffect] = {}
        self.draw_list = []
        for composited_layer in self.composited_layers:
            current_effect: Any = \
                DrawCompositedLayer(composited_layer)
            if not composited_layer.display_items:
                continue
            parent = composited_layer.display_items[0].parent
            while parent:
                new_parent = self.get_latest(parent)
                if new_parent in new_effects:
                    new_effects[new_parent].children.append(
                        current_effect)
                    break
                else:
                    current_effect = \
                        new_parent.clone(current_effect)
                    new_effects[new_parent] = current_effect
                    parent = parent.parent
            if not parent:
                self.draw_list.append(current_effect)

        if self.pending_hover and self.accessibility_tree:
            (x, y) = self.pending_hover
            y += self.active_tab_scroll
            a11y_node = self.accessibility_tree.hit_test(x, y)
            if a11y_node:
                if not self.hovered_a11y_node or \
                        a11y_node.node != self.hovered_a11y_node.node:
                    self.needs_speak_hovered_node = True
                self.hovered_a11y_node = a11y_node
        self.pending_hover = None

        if self.hovered_a11y_node:
            for bound in self.hovered_a11y_node.bounds:
                self.draw_list.append(DrawOutline(
                    bound,
                    "white" if self.dark_mode else "black", 2))

    def raster_tab(self):
        for composited_layer in self.composited_layers:
            composited_layer.raster()

    def raster_chrome(self):
        canvas = self.chrome_surface.getCanvas()
        if self.dark_mode:
            background_color = skia.ColorBLACK
        else:
            background_color = skia.ColorWHITE
        canvas.clear(background_color)

        for cmd in self.chrome.paint():
            cmd.execute(canvas)

    def draw(self):
        canvas = self.root_surface.getCanvas()
        if self.dark_mode:
            canvas.clear(skia.ColorBLACK)
        else:
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
        if not self.needs_composite and \
                len(self.composited_updates) == 0 \
                and not self.needs_raster and not self.needs_draw and not \
                self.needs_accessibility:
            self.lock.release()
            return

        self.measure.time('composite_raster_and_draw')
        if self.needs_composite:
            self.measure.time('composite')
            self.composite()
            self.measure.stop('composite')
        if self.needs_raster:
            self.measure.time('raster')
            self.raster_chrome()
            self.raster_tab()
            self.measure.stop('raster')
        if self.needs_draw:
            self.measure.time('draw')
            self.paint_draw_list()
            self.draw()
            self.measure.stop('draw')
        self.measure.stop('composite_raster_and_draw')

        if self.needs_accessibility:
            self.update_accessibility()

        self.needs_composite = False
        self.needs_raster = False
        self.needs_draw = False
        self.needs_accessibility = False
        self.lock.release()
