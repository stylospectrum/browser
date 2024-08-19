import skia

from typing import TYPE_CHECKING

from utils import get_font, linespace
from constants import  WIDTH, DEFAULT_URL
from url import URL
from draw_command import DrawLine, DrawText, DrawOutline, DrawRect, PaintCommand
from task import Task

if TYPE_CHECKING:
    from browser import Browser

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

