"""Read Out Loud with the voices built into Windows (SAPI), offline.

Falls back to Qt's text-to-speech module when it is installed (not part of
PySide6-Essentials); otherwise reading aloud is unavailable.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QObject, QTimer, Signal


def page_speech_text(doc, pno: int) -> str:
    """The page text as sentences: lines joined, hyphenated breaks undone."""
    text = doc.page_text(pno).text
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"(?<![.!?:;])\n(?!\n)", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


class _SapiBackend:
    """Windows SAPI 5 (SAPI.SpVoice) through pywin32."""

    ASYNC, PURGE = 1, 2

    def __init__(self):
        import win32com.client
        self.voice = win32com.client.Dispatch("SAPI.SpVoice")

    def voices(self) -> list[str]:
        try:
            return [v.GetDescription() for v in self.voice.GetVoices()]
        except Exception:
            return []

    def set_voice(self, name: str):
        for v in self.voice.GetVoices():
            if v.GetDescription() == name:
                self.voice.Voice = v
                return

    def set_rate(self, rate: int):
        self.voice.Rate = max(-10, min(10, int(rate)))

    def say(self, text: str):
        self.voice.Speak(text, self.ASYNC | self.PURGE)

    def busy(self) -> bool:
        return self.voice.Status.RunningState == 2  # SRSEIsSpeaking

    def pause(self):
        self.voice.Pause()

    def resume(self):
        self.voice.Resume()

    def stop(self):
        self.voice.Speak("", self.ASYNC | self.PURGE)


class _QtBackend:
    def __init__(self):
        from PySide6.QtTextToSpeech import QTextToSpeech
        self.tts = QTextToSpeech()

    def voices(self):
        return [v.name() for v in self.tts.availableVoices()]

    def set_voice(self, name):
        for v in self.tts.availableVoices():
            if v.name() == name:
                self.tts.setVoice(v)

    def set_rate(self, rate):
        self.tts.setRate(max(-1.0, min(1.0, rate / 10)))

    def say(self, text):
        self.tts.say(text)

    def busy(self):
        from PySide6.QtTextToSpeech import QTextToSpeech
        return self.tts.state() in (QTextToSpeech.State.Speaking, QTextToSpeech.State.Synthesizing)

    def pause(self):
        self.tts.pause()

    def resume(self):
        self.tts.resume()

    def stop(self):
        self.tts.stop()


def make_backend():
    for cls in (_SapiBackend, _QtBackend):
        try:
            return cls()
        except Exception:
            continue
    return None


class Reader(QObject):
    """Reads pages one after another and reports which page it is on."""

    stateChanged = Signal(str)     # "speaking", "paused", "stopped"
    pageStarted = Signal(object, int)   # doc, page

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.backend = backend if backend is not None else make_backend()
        self.state = "stopped"
        self._queue: list[int] = []
        self._doc = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)

    @property
    def available(self) -> bool:
        return self.backend is not None

    def read(self, doc, pages: list[int]):
        if not self.available:
            return False
        self.stop()
        self._doc = doc
        self._queue = list(pages)
        self._next()
        return True

    def _next(self):
        while self._queue:
            pno = self._queue.pop(0)
            text = page_speech_text(self._doc, pno)
            if text:
                self.pageStarted.emit(self._doc, pno)
                self.backend.say(text)
                self._set("speaking")
                self._timer.start()
                return
        self.stop()

    def _poll(self):
        if self.state == "speaking" and not self.backend.busy():
            self._next()

    def toggle_pause(self):
        if self.state == "speaking":
            self.backend.pause()
            self._set("paused")
        elif self.state == "paused":
            self.backend.resume()
            self._set("speaking")

    def stop(self):
        self._timer.stop()
        self._queue = []
        if self.backend is not None and self.state != "stopped":
            try:
                self.backend.stop()
            except Exception:
                pass
        self._set("stopped")

    def _set(self, state):
        if state != self.state:
            self.state = state
            self.stateChanged.emit(state)
