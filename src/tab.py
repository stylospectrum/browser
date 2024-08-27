import math

from typing import TYPE_CHECKING, Union, cast

from frame import Frame
from draw_command import PaintCommand, Blend, VisualEffect
from a11y import AccessibilityNode
from layout import IframeLayout
from url import URL
from node import Element
from task import TaskRunner
from js_engine import JSContext
from constants import WIDTH
from utils import tree_to_list, print_tree

if TYPE_CHECKING:
    from browser import Browser


def paint_tree(layout_object, display_list: list[PaintCommand]):
    cmds: list[PaintCommand] = []
    if layout_object.should_paint():
        cmds = layout_object.paint()

    if isinstance(layout_object, IframeLayout) and \
            layout_object.node.frame and \
            layout_object.node.frame.loaded:
        paint_tree(layout_object.node.frame.document, cmds)
    else:
        for child in layout_object.children:
            paint_tree(child, cmds)

    if layout_object.should_paint():
        cmds = layout_object.paint_effects(cmds)
    display_list.extend(cmds)


class CommitData:
    def __init__(self, url: URL, scroll: int, root_frame_focused: Frame, height: int, display_list: list[Union[VisualEffect, PaintCommand]], composited_updates: Union[None, dict[Element, Blend]], accessibility_tree: Union[AccessibilityNode, None], focus: Element):
        self.url = url
        self.scroll = scroll
        self.root_frame_focused = root_frame_focused
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
        self.tab_height = tab_height
        self.history: list[URL] = []
        self.focus: Union[Element, None] = None
        self.focused_frame: Union[Frame, None] = None
        self.task_runner = TaskRunner(self)
        self.needs_paint = False
        self.browser = browser
        self.dark_mode: bool = browser.dark_mode
        self.needs_accessibility = False
        self.accessibility_tree: Union[AccessibilityNode, None] = None
        self.composited_updates: list[Element] = []
        self.root_frame: Union[Frame, None] = None
        self.window_id_to_frame: dict[int, Frame] = {}
        self.origin_to_js: dict[str, JSContext] = {}
        self.loaded = False
        self.task_runner.start_thread()

    def post_message(self, message: str, target_window_id: int):
        frame = self.window_id_to_frame[target_window_id]
        frame.js.dispatch_post_message(  # type: ignore
            message, target_window_id)

    def zoom_by(self, increment: bool):
        if increment:
            self.zoom *= 1.1
            self.scroll *= 1.1
        else:
            self.zoom *= 1/1.1
            self.scroll *= 1/1.1
        for id, frame in self.window_id_to_frame.items():
            frame.document.zoom.mark()
        self.root_frame.scroll_changed_in_frame = True  # type: ignore
        self.set_needs_render_all_frames()

    def reset_zoom(self):
        self.scroll /= self.zoom
        self.zoom = 1
        for id, frame in self.window_id_to_frame.items():
            frame.document.zoom.mark()
        self.root_frame.scroll_changed_in_frame = True  # type: ignore
        self.set_needs_render_all_frames()

    def get_js(self, url: URL):
        origin = url.origin()
        if origin not in self.origin_to_js:
            self.origin_to_js[origin] = JSContext(self, origin)
        return self.origin_to_js[origin]

    def advance_tab(self):
        frame = self.focused_frame or self.root_frame
        frame.advance_tab()

    def keypress(self, char):
        frame = self.focused_frame
        if not frame:
            frame = self.root_frame
        frame.keypress(char)

    def click(self, x, y):
        self.render()
        self.root_frame.click(x, y)

    def enter(self):
        if self.focus:
            frame = self.focused_frame or self.root_frame
            frame.activate_element(self.focus)

    def scroll_down(self):
        frame = self.focused_frame or self.root_frame
        frame.scroll_down()
        self.set_needs_paint()

    def set_dark_mode(self, val: bool):
        self.dark_mode = val
        self.set_needs_render_all_frames()

    def set_needs_render_all_frames(self):
        for id, frame in self.window_id_to_frame.items():
            frame.set_needs_render()

    def set_needs_paint(self):
        self.needs_paint = True
        self.browser.set_needs_animation_frame(self)

    def run_animation_frame(self, scroll):
        if not self.root_frame.scroll_changed_in_frame:
            self.root_frame.scroll = scroll

        needs_composite = False
        for (window_id, frame) in self.window_id_to_frame.items():
            if not frame.loaded:
                continue

            self.browser.measure.time('script-runRAFHandlers')
            frame.js.dispatch_RAF(frame.window_id)
            self.browser.measure.stop('script-runRAFHandlers')

            for node in tree_to_list(frame.nodes, []):
                for (property_name, animation) in \
                        node.animations.items():
                    value = animation.animate()
                    if value:
                        node.style[property_name].set(value)
                        self.composited_updates.append(node)
                        self.set_needs_paint()

                    if animation.frame_count + 1 >= animation.num_frames:
                        self.browser.needs_animation_frame = False

            if frame.needs_style or frame.needs_layout:
                needs_composite = True

        self.render()

        for (window_id, frame) in self.window_id_to_frame.items():
            if frame == self.root_frame:
                continue
            if frame.scroll_changed_in_frame:
                needs_composite = True
                frame.scroll_changed_in_frame = False

        if self.focus and self.focused_frame.needs_focus_scroll:
            self.focused_frame.scroll_to(self.focus)
            self.focused_frame.needs_focus_scroll = False

        composited_updates = None
        if not needs_composite:
            composited_updates = {}
            for node in self.composited_updates:
                composited_updates[node] = node.blend_op
        self.composited_updates = []

        scroll = None
        if self.root_frame.scroll_changed_in_frame:
            scroll = self.root_frame.scroll

        root_frame_focused = not self.focused_frame or \
            self.focused_frame == self.root_frame
        commit_data = CommitData(
            cast(URL, self.root_frame.url), scroll, root_frame_focused, math.ceil(self.root_frame.document.height), self.display_list, composited_updates, self.accessibility_tree, self.focus)
        self.display_list = None
        self.root_frame.scroll_changed_in_frame = False
        self.browser.commit(self, commit_data)

    def load(self, url: URL, payload=None):
        self.loaded = False
        self.history.append(url)
        self.task_runner.clear_pending_tasks()
        self.root_frame = Frame(self, None, None)
        self.root_frame.load(url, payload)
        self.root_frame.frame_width = WIDTH
        self.root_frame.frame_height = self.tab_height
        self.loaded = True

    def render(self):
        self.browser.measure.time('render')

        for id, frame in self.window_id_to_frame.items():
            if frame.loaded:
                frame.render()

        if self.needs_accessibility:
            self.accessibility_tree = AccessibilityNode(self.root_frame.nodes)
            self.accessibility_tree.build()
            self.needs_accessibility = False
            self.needs_paint = True

        if self.needs_paint:
            self.display_list = []
            paint_tree(self.root_frame.document, self.display_list)
            self.needs_paint = False

        self.browser.measure.stop('render')

    def go_back(self):
        if len(self.history) > 1:
            self.history.pop()
            back = self.history.pop()
            self.load(back)

    def __repr__(self):
        return "Tab(history={})".format(self.history)
