import dukpy  # type: ignore
import threading

from typing import cast, TYPE_CHECKING

from css_parser import CSSParser
from html_parser import HTMLParser
from utils import tree_to_list
from node import Element
from url import URL
from task import Task

if TYPE_CHECKING:
    from browser import Tab

EVENT_DISPATCH_JS = \
    "new Node(dukpy.handle).dispatchEvent(new Event(dukpy.type))"
SETTIMEOUT_JS = "__runSetTimeout(dukpy.handle)"
XHR_ONLOAD_JS = "__runXHROnload(dukpy.out, dukpy.handle)"
RUNTIME_JS = open("src/default/runtime.js").read()


class JSContext:
    def __init__(self, tab: 'Tab'):
        self.tab = tab
        self.discarded = False
        self.interp = dukpy.JSInterpreter()
        self.node_to_handle: dict[Element, int] = {}
        self.handle_to_node: dict[int, Element] = {}

        self.interp.export_function("log", print)
        self.interp.export_function("getAttribute", self.getAttribute)
        self.interp.export_function("querySelectorAll",
                                    self.querySelectorAll)
        self.interp.export_function("XMLHttpRequest_send",
                                    self.XMLHttpRequest_send)
        self.interp.export_function("innerHTML_set",
                                    self.innerHTML_set)
        self.interp.export_function("setTimeout",
                                    self.setTimeout)
        self.interp.export_function("requestAnimationFrame",
                                    self.requestAnimationFrame)
        self.interp.export_function("style_set", self.style_set)

        self.tab.browser.measure.time('script-runtime')
        self.interp.evaljs(RUNTIME_JS)
        self.tab.browser.measure.stop('script-runtime')

    def requestAnimationFrame(self):
        self.tab.browser.set_needs_animation_frame(self.tab)

    def dispatch_xhr_onload(self, out: str, handle: int):
        if self.discarded:
            return
        self.tab.browser.measure.time('script-xhr')
        do_default = self.interp.evaljs(
            XHR_ONLOAD_JS, out=out, handle=handle)
        self.tab.browser.measure.stop('script-xhr')

    def dispatch_settimeout(self, handle: int):
        if self.discarded:
            return
        self.tab.browser.measure.time('script-settimeout')
        self.interp.evaljs(SETTIMEOUT_JS, handle=handle)
        self.tab.browser.measure.stop('script-settimeout')

    def setTimeout(self, handle: int, time: int):
        def run_callback():
            task = Task(self.dispatch_settimeout, handle)
            self.tab.task_runner.schedule_task(task)
        threading.Timer(time / 1000.0, run_callback).start()

    def XMLHttpRequest_send(self, method: str, url: str, body: str, is_async: bool, handle: int):
        full_url = cast(URL, self.tab.url).resolve(url)
        if not self.tab.allowed_request(full_url):
            raise Exception("Cross-origin XHR blocked by CSP")
        if full_url.origin() != cast(URL, self.tab.url).origin():
            raise Exception("Cross-origin XHR request not allowed")

        def run_load():
            headers, response = full_url.request(self.tab.url, body)
            task = Task(self.dispatch_xhr_onload, response, handle)
            self.tab.task_runner.schedule_task(task)
            return response

        if not is_async:
            return run_load()
        else:
            threading.Thread(target=run_load).start()

    def innerHTML_set(self, handle, s: str):
        doc = HTMLParser("<html><body>" + s + "</body></html>").parse()
        new_nodes = doc.children[0].children

        elt = self.handle_to_node[handle]
        elt.children = new_nodes
        for child in elt.children:
            child.parent = elt

        self.tab.set_needs_render()

    def style_set(self, handle: int, s: str):
        elt = self.handle_to_node[handle]
        elt.attributes["style"] = s
        self.tab.set_needs_render()

    def dispatch_event(self, type: str, elt: Element):
        handle = self.node_to_handle.get(elt, -1)
        do_default = self.interp.evaljs(
            EVENT_DISPATCH_JS, type=type, handle=handle)
        return not do_default

    def get_handle(self, elt: Element):
        if elt not in self.node_to_handle:
            handle = len(self.node_to_handle)
            self.node_to_handle[elt] = handle
            self.handle_to_node[handle] = elt
        else:
            handle = self.node_to_handle[elt]
        return handle

    def getAttribute(self, handle: int, attr: str):
        elt = self.handle_to_node[handle]
        attr = elt.attributes.get(attr, '')
        return attr

    def querySelectorAll(self, selector_text: str):
        selector = CSSParser(selector_text).selector()

        nodes = cast(list[Element], [node for node
                                     in tree_to_list(self.tab.nodes, [])
                                     if selector.matches(node)])
        return [self.get_handle(node) for node in nodes]

    def run(self, script: str, code: str):
        try:
            self.tab.browser.measure.time('script-load')
            self.interp.evaljs(code)
            self.tab.browser.measure.stop('script-load')
        except dukpy.JSRuntimeError as e:
            self.tab.browser.measure.stop('script-load')
            print("Script", script, "crashed", e)
