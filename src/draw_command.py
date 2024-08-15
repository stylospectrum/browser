import skia  # type: ignore

from abc import ABC, abstractmethod
from utils import parse_blend_mode, parse_color, linespace

class DrawCommand(ABC):
    @abstractmethod
    def execute(self, canvas):
        pass

class Blend(DrawCommand):
    def __init__(self, opacity: float, blend_mode, children: list):
        self.opacity = opacity
        self.blend_mode = blend_mode
        self.should_save = self.blend_mode or self.opacity < 1

        self.children = children
        self.rect = skia.Rect.MakeEmpty()
        for cmd in self.children:
            self.rect.join(cmd.rect)

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


class DrawOutline(DrawCommand):
    def __init__(self, rect, color: str, thickness: int):
        self.rect = rect
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


class DrawLine(DrawCommand):
    def __init__(self, x1: int, y1: int, x2: int, y2: int, color: str, thickness: int):
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.color = color
        self.thickness = thickness
        self.rect = skia.Rect.MakeLTRB(x1, y1, x2, y2)

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


class DrawText(DrawCommand):
    def __init__(self, x1: int, y1: int, text: str, font, color: str):
        self.top = y1
        self.left = x1
        self.right = x1 + font.measureText(text)
        self.bottom = y1 + linespace(font)
        self.text = text
        self.font = font
        self.color = color
        self.rect = \
            skia.Rect.MakeLTRB(x1, y1, self.right, self.bottom)

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


class DrawRect(DrawCommand):
    def __init__(self, rect, color: str):
        self.rect = rect
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


class DrawRRect(DrawCommand):
    def __init__(self, rect, radius, color):
        self.rect = rect
        self.rrect = skia.RRect.MakeRectXY(rect, radius, radius)
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
        )
        canvas.drawRRect(self.rrect, paint)
