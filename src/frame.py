from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tab import Tab

class Frame:
    def __init__(self, tab: 'Tab', parent_frame: 'Frame', frame_element):
        self.tab = tab
        self.parent_frame = parent_frame
        self.frame_element = frame_element