import skia  # type: ignore

from draw_command import PaintCommand


class CompositedLayer:
    def __init__(self, skia_context, display_item: PaintCommand):
        self.skia_context = skia_context
        self.surface = None
        self.display_items = [display_item]

    def composited_bounds(self):
        rect = skia.Rect.MakeEmpty()
        for item in self.display_items:
            rect.join(item.rect)
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
