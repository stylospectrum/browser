import skia

DEFAULT_URL = 'https://browser.engineering/invalidation.html'
BROKEN_IMAGE = skia.Image.open("./Broken_Image.png")
WIDTH, HEIGHT = 800, 400
IFRAME_WIDTH_PX, IFRAME_HEIGHT_PX = 300, 150
H_STEP, V_STEP = 13.0, 18.0
INPUT_WIDTH_PX = 200
SCROLL_STEP = 100
REFRESH_RATE_SEC = .033
SHOW_COMPOSITED_LAYER_BORDERS = False

INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "color": "black",
}

NAMED_COLORS = {
    "black": "#000000",
    "gray":  "#808080",
    "white": "#ffffff",
    "red":   "#ff0000",
    "green": "#00ff00",
    "blue":  "#0000ff",
    "lightblue": "#add8e6",
    "lightgreen": "#90ee90",
    "orange": "#ffa500",
    "orangered": "#ff4500",
}

BLOCK_ELEMENTS = [
    "html", "body", "article", "section", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
    "footer", "address", "p", "hr", "pre", "blockquote",
    "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
    "figcaption", "main", "div", "table", "form", "fieldset",
    "legend", "details", "summary"
]

CSS_PROPERTIES = {
    "font-size": "inherit", "font-weight": "inherit",
    "font-style": "inherit", "color": "inherit",
    "opacity": "1.0", "transition": "",
    "transform": "none", "mix-blend-mode": None,
    "border-radius": "0px", "overflow": "visible",
    "outline": "none", "background-color": "transparent",
    "image-rendering": "auto",
}
