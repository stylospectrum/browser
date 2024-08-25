import skia

from typing import Union

from node import Element
from utils import parse_blend_mode, parse_color, linespace, map_translation, parse_image_rendering


class VisualEffect:
    def __init__(self, rect, children: list[Union['PaintCommand', 'VisualEffect']], node: Union[Element, None]):
        self.rect = rect.makeOffset(0.0, 0.0)
        self.children = children
        for child in self.children:
            self.rect.join(child.rect)
        self.node = node
        self.parent: 'VisualEffect'
        self.needs_compositing = any([
            child.needs_compositing for child in self.children  # type: ignore
        ])


class PaintCommand:
    def __init__(self, rect):
        self.rect = rect
        self.children = []
        self.parent: VisualEffect
        self.needs_compositing = False


class Blend(VisualEffect):
    def __init__(self, opacity: float, blend_mode: Union[str, None], node, children: list):
        super().__init__(skia.Rect.MakeEmpty(), children, node)
        self.opacity = opacity
        self.node = node
        self.blend_mode = blend_mode
        self.should_save = self.blend_mode or self.opacity < 1

        if self.should_save:
            self.needs_compositing = True

    def execute(self, canvas):
        paint = skia.Paint(
            Alphaf=self.opacity,
            BlendMode=parse_blend_mode(self.blend_mode),
        )
        if self.should_save:
            canvas.saveLayer(None, paint)
        for cmd in self.children:
            cmd.execute(canvas)
        if self.should_save:
            canvas.restore()

    def clone(self, child):
        return Blend(self.opacity, self.blend_mode,
                     self.node, [child])

    def map(self, rect):
        if self.children and \
           isinstance(self.children[-1], Blend) and \
           self.children[-1].blend_mode == "destination-in":
            bounds = rect.makeOffset(0.0, 0.0)
            bounds.intersect(self.children[-1].rect)
            return bounds
        else:
            return rect

    def unmap(self, rect):
        return rect

    def __repr__(self):
        args = ""
        if self.opacity < 1:
            args += ", opacity={}".format(self.opacity)
        if self.blend_mode:
            args += ", blend_mode={}".format(self.blend_mode)
        if not args:
            args = ", <no-op>"
        return "Blend({})".format(args[2:])


class Transform(VisualEffect):
    def __init__(self, translation: tuple[float, float], rect, node: Element, children: list):
        super().__init__(rect, children, node)
        self.self_rect = rect
        self.translation = translation

    def execute(self, canvas):
        if self.translation:
            (x, y) = self.translation
            canvas.save()
            canvas.translate(x, y)
        for cmd in self.children:
            cmd.execute(canvas)
        if self.translation:
            canvas.restore()

    def clone(self, child):
        return Transform(self.translation, self.self_rect,
                         self.node, [child])

    def map(self, rect):
        return map_translation(rect, self.translation)

    def unmap(self, rect):
        return map_translation(rect, self.translation, True)

    def __repr__(self):
        if self.translation:
            (x, y) = self.translation
            return "Transform(translate({}, {}))".format(x, y)
        else:
            return "Transform(<no-op>)"


class DrawOutline(PaintCommand):
    def __init__(self, rect, color: str, thickness: int):
        super().__init__(rect)
        self.color = color
        self.thickness = thickness

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
            StrokeWidth=self.thickness,
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawRect(self.rect, paint)

    def __repr__(self):
        return "DrawOutline({}, {}, {}, {}, color={}, thickness={})".format(
            self.rect.left(), self.rect.top(), self.rect.right(), self.rect.bottom(),
            self.color, self.thickness)


class DrawLine(PaintCommand):
    def __init__(self, x1: float, y1: float, x2: float, y2: float, color: str, thickness: float):
        super().__init__(skia.Rect.MakeLTRB(x1, y1, x2, y2))
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.color = color
        self.thickness = thickness

    def execute(self, canvas):
        path = skia.Path().moveTo(self.x1, self.y1) \
                          .lineTo(self.x2, self.y2)
        paint = skia.Paint(
            Color=parse_color(self.color),
            StrokeWidth=self.thickness,
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawPath(path, paint)

    def __repr__(self):
        return "DrawLine({}, {}, {}, {}, color={}, thickness={})".format(
            self.react.left(), self.react.top(), self.react.right(), self.react.bottom(),
            self.color, self.thickness)


class DrawText(PaintCommand):
    def __init__(self, x1: int, y1: int, text: str, font, color: str):
        self.top = y1
        self.left = x1
        self.right = x1 + font.measureText(text)
        self.bottom = y1 + linespace(font)
        self.text = text
        self.font = font
        self.color = color
        super().__init__(skia.Rect.MakeLTRB(x1, y1,
                                            self.right, self.bottom))

    def execute(self, canvas):
        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
        )
        baseline = self.top - self.font.getMetrics().fAscent
        canvas.drawString(self.text, float(self.left), baseline,
                          self.font, paint)

    def __repr__(self):
        return "DrawText(text={})".format(self.text)


class DrawRect(PaintCommand):
    def __init__(self, rect, color: str):
        super().__init__(rect)
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
        )
        canvas.drawRect(self.rect, paint)

    def __repr__(self):
        return "DrawRect(top={} left={} bottom={} right={} color={})".format(
            self.rect.top(), self.rect.left(), self.rect.bottom(),
            self.rect.right(), self.color)


class DrawRRect(PaintCommand):
    def __init__(self, rect, radius, color):
        super().__init__(rect)
        self.rrect = skia.RRect.MakeRectXY(rect, radius, radius)
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
        )
        canvas.drawRRect(self.rrect, paint)

    def __repr__(self):
        return "DrawRRect(rect={}, color={})".format(
            str(self.rrect), self.color)


class DrawImage(PaintCommand):
    def __init__(self, image, rect, quality: str):
        super().__init__(rect)
        self.image = image
        self.quality = parse_image_rendering(quality)

    def execute(self, canvas):
        paint = skia.Paint(
            FilterQuality=self.quality,
        )
        canvas.drawImageRect(self.image, self.rect, paint)

    def __repr__(self):
        return "DrawImage(rect={})".format(
            self.rect)
