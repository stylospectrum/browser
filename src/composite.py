import skia  # type: ignore

from typing import Union

from draw_command import PaintCommand, DrawOutline, VisualEffect
from constants import SHOW_COMPOSITED_LAYER_BORDERS
from utils import local_to_absolute, absolute_to_local

DisplayItem = Union[VisualEffect, PaintCommand]

class CompositedLayer:
    def __init__(self, skia_context, display_item: DisplayItem):
        self.skia_context = skia_context
        self.surface = None
        self.display_items = [display_item]

    def add(self, display_item: DisplayItem):
        self.display_items.append(display_item)

    def can_merge(self, display_item: DisplayItem):
        return display_item.parent == \
            self.display_items[0].parent

    def absolute_bounds(self):
        rect = skia.Rect.MakeEmpty()
        for item in self.display_items:
            rect.join(local_to_absolute(item, item.rect))
        return rect

    def composited_bounds(self):
        rect = skia.Rect.MakeEmpty()
        for item in self.display_items:
            rect.join(absolute_to_local(
                item, local_to_absolute(item, item.rect)))
        rect.outset(1, 1)
        return rect

    def raster(self):
        bounds = self.composited_bounds()
        if bounds.isEmpty():
            return
        irect = bounds.roundOut()

        if not self.surface:
            self.surface = skia.Surface.MakeRenderTarget(
                self.skia_context, skia.Budgeted.kNo,
                skia.ImageInfo.MakeN32Premul(
                    irect.width(), irect.height()))
            assert self.surface

        canvas = self.surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.save()
        canvas.translate(-bounds.left(), -bounds.top())

        for item in self.display_items:
            item.execute(canvas)
        canvas.restore()

        if SHOW_COMPOSITED_LAYER_BORDERS:
            border_rect = skia.Rect.MakeXYWH(
                1, 1, irect.width() - 2, irect.height() - 2)
            DrawOutline(border_rect, "red", 1).execute(canvas)


class DrawCompositedLayer(PaintCommand):
    def __init__(self, composited_layer: CompositedLayer):
        self.composited_layer = composited_layer
        super().__init__(
            self.composited_layer.composited_bounds())

    def execute(self, canvas):
        layer = self.composited_layer
        bounds = layer.composited_bounds()
        layer.surface.draw(canvas, bounds.left(), bounds.top())

    def __repr__(self):
        return "DrawCompositedLayer()"
