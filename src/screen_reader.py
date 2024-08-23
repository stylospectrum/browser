import os
import gtts
import playsound

from typing import TYPE_CHECKING

from a11y import AccessibilityNode
from utils import tree_to_list

if TYPE_CHECKING:
    from browser import Browser

SPEECH_FILE = "./speech-fragment.mp3"

class ScreenReader:
    def __init__(self, browser: 'Browser') -> None:
        self.browser = browser
        self.has_spoken_document = False

    def speak_text(self, text: str):
        print("SPEAK:", text)
        tts = gtts.gTTS(text)
        tts.save(SPEECH_FILE)
        playsound.playsound(SPEECH_FILE)
        os.remove(SPEECH_FILE)

    def speak_document(self):
        text = "Here are the document contents: "
        tree_list = tree_to_list(self.browser.accessibility_tree, [])
        for accessibility_node in tree_list:
            new_text = accessibility_node.text
            if new_text:
                text += "\n"  + new_text

        self.speak_text(text)

    def speak_node(self, node: AccessibilityNode, text: str):
        text += node.text
        if text and node.children and \
            node.children[0].role == "StaticText":
            text += " " + \
            node.children[0].text

        if text:
            self.speak_text(text)