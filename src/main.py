import sys
import ctypes
import sdl2

from browser import Browser
from url import URL
from constants import DEFAULT_URL


def mainloop(browser: Browser):
    event = sdl2.SDL_Event()
    ctrl_down = False

    while True:
        while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl2.SDL_QUIT:
                browser.handle_quit()
                sdl2.SDL_Quit()
                sys.exit()
            elif event.type == sdl2.SDL_MOUSEBUTTONUP:
                browser.handle_click(event.button)
            elif event.type == sdl2.SDL_KEYDOWN:
                if ctrl_down:
                    if event.key.keysym.sym == sdl2.SDLK_EQUALS:
                        browser.increment_zoom(True)
                    elif event.key.keysym.sym == sdl2.SDLK_MINUS:
                        browser.increment_zoom(False)
                    elif event.key.keysym.sym == sdl2.SDLK_0:
                        browser.reset_zoom()
                    elif event.key.keysym.sym == sdl2.SDLK_d:
                        browser.toggle_dark_mode()
                    elif event.key.keysym.sym == sdl2.SDLK_LEFT:
                        browser.go_back()
                    elif event.key.keysym.sym == sdl2.SDLK_l:
                        browser.focus_addressbar()
                    elif event.key.keysym.sym == sdl2.SDLK_t:
                        browser.new_tab(URL(DEFAULT_URL))
                    elif event.key.keysym.sym == sdl2.SDLK_TAB:
                        browser.cycle_tabs()
                    elif event.key.keysym.sym == sdl2.SDLK_q:
                        browser.handle_quit()
                        sdl2.SDL_Quit()
                        sys.exit()
                        break
                    elif event.key.keysym.sym == sdl2.SDLK_a:
                        browser.toggle_accessibility()
                elif event.key.keysym.sym == sdl2.SDLK_RETURN:
                    browser.handle_enter()
                elif event.key.keysym.sym == sdl2.SDLK_DOWN:
                    browser.handle_down()
                elif event.key.keysym.sym == sdl2.SDLK_RCTRL or \
                        event.key.keysym.sym == sdl2.SDLK_LCTRL:
                    ctrl_down = True
                elif event.key.keysym.sym == sdl2.SDLK_RETURN:
                    browser.handle_enter()
                elif event.key.keysym.sym == sdl2.SDLK_TAB:
                    browser.handle_tab()
            elif event.type == sdl2.SDL_KEYUP:
                if event.key.keysym.sym == sdl2.SDLK_RCTRL or event.key.keysym.sym == sdl2.SDLK_LCTRL:
                    ctrl_down = False
            elif event.type == sdl2.SDL_TEXTINPUT:
                browser.handle_key(event.text.text.decode('utf8'))
            elif event.type == sdl2.SDL_MOUSEMOTION:
                browser.handle_hover(event.motion)
        browser.composite_raster_and_draw()
        browser.schedule_animation_frame()


if __name__ == "__main__":
    sdl2.SDL_Init(sdl2.SDL_INIT_EVENTS)
    browser = Browser()
    browser.new_tab(URL(DEFAULT_URL))
    mainloop(browser)
