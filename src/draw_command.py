import skia  # type: ignore

from abc import ABC, abstractmethod
from typing import Union

from utils import parse_blend_mode, parse_color, linespace


class PaintCommand(ABC):
    def __init__(self, rect):
        self.rect = rect
        self.children = []
        self.parent = None

    @abstractmethod
    def execute(self, canvas):
        pass


class VisualEffect(ABC):
    def __init__(self, rect, children: list, node=None):
        self.rect = rect.makeOffset(0.0, 0.0)
        self.children = children
        self.node = node
        for child in self.children:
            self.rect.join(child.rect)

    @abstractmethod
    def execute(self, canvas):
        pass


class Blend(VisualEffect):
    def __init__(self, opacity: float, blend_mode: Union[str, None], node, children: list):
        super().__init__(skia.Rect.MakeEmpty(), children, node)
        self.opacity = opacity
        self.node = node
        self.blend_mode = blend_mode
        self.should_save = self.blend_mode or self.opacity < 1

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

    def __repr__(self):
        args = ""
        if self.opacity < 1:
            args += ", opacity={}".format(self.opacity)
        if self.blend_mode:
            args += ", blend_mode={}".format(self.blend_mode)
        if not args:
            args = ", <no-op>"
        return "Blend({})".format(args[2:])


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
    def __init__(self, x1: int, y1: int, x2: int, y2: int, color: str, thickness: int):
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
