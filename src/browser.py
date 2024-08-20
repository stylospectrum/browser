import math
import threading
import OpenGL.GL
import sdl2
import skia

from typing import Any

from utils import tree_to_list, add_parent_pointers, local_to_absolute, print_tree
from constants import SCROLL_STEP, WIDTH, HEIGHT, REFRESH_RATE_SEC
from url import URL
from draw_command import PaintCommand
from composite import CompositedLayer, DrawCompositedLayer
from task import Task
from measure import MeasureTime
from tab import Tab, CommitData
from chrome import Chrome
from draw_command import Blend, VisualEffect


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
        self.needs_composite = False
        self.needs_raster = False
        self.needs_draw = False
        self.needs_animation_frame = True
        self.animation_timer = None
        self.active_tab_url = None
        self.active_tab_scroll = 0
        self.active_tab_height = 0
        self.active_tab_display_list = None
        self.composited_layers: list[CompositedLayer] = []
        self.draw_list: list[VisualEffect] = []
        self.composited_updates = {}

        threading.current_thread().name = "Browser thread"

    def clear_data(self):
        self.active_tab_scroll = 0
        self.active_tab_url = None
        self.active_tab_display_list = []
        self.composited_layers = []
        self.composited_updates = {}

    def set_active_tab(self, tab):
        self.active_tab = tab
        self.clear_data()
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
            self.active_tab_height = data.height
            self.animation_timer = None

            if data.scroll != None:
                self.active_tab_scroll = data.scroll

            if data.display_list:
                self.active_tab_display_list = data.display_list

            self.composited_updates = data.composited_updates
            if self.composited_updates == None:
                self.composited_updates = {}
                self.set_needs_composite()
            else:
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
        if not self.active_tab_height:
            self.lock.release()
            return
        self.active_tab_scroll = self.clamp_scroll(
            self.active_tab_scroll + SCROLL_STEP)
        self.set_needs_raster()
        self.needs_animation_frame = True
        self.lock.release()

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
        self.lock.release()

    def handle_quit(self):
        self.measure.finish()
        for tab in self.tabs:
            tab.task_runner.set_needs_quit()
        sdl2.SDL_GL_DeleteContext(self.gl_context)
        sdl2.SDL_DestroyWindow(self.sdl_window)

    def composite(self) -> None:
        self.composited_layers = []
        add_parent_pointers(self.active_tab_display_list)
        all_commands: list[VisualEffect] = []

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
        if not self.needs_composite and \
                not self.needs_raster and \
                not self.needs_draw:
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
            print(self.composited_layers)
            self.measure.stop('draw')
        self.measure.stop('composite_raster_and_draw')
        self.needs_composite = False
        self.needs_raster = False
        self.needs_draw = False
        self.lock.release()
