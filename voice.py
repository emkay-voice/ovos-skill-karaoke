"""The device's own voice, as samples - on any OVOS.

  * ovos-audio answers `speak:b64_audio` with the WAV of a sentence spoken
    by the configured TTS (also in Docker or over HiveMind, where the skill
    can't load the TTS itself);
  * where nothing answers that (Emkay, or ovos-audio not running), the
    configured TTS plugin is loaded here, and its `fallback_module` if the
    main one can't speak yet.
"""
from __future__ import annotations

import base64
import io
import tempfile
import wave

import numpy as np
from ovos_bus_client.message import Message


def wav_samples(data: bytes):
    """Float samples and rate of a WAV (other formats via ffmpeg, if there)."""
    if data[:4] != b"RIFF":
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            raise RuntimeError("the TTS gives no WAV and ffmpeg isn't installed")
        data = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "wav", "pipe:1"],
                              input=data, capture_output=True, check=True, timeout=60).stdout
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if width == 1:
        x = (x - 128) / 128
    else:
        x /= float(2 ** (8 * width - 1))
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


class BusVoice:
    """ovos-audio's TTS over the messagebus."""

    def __init__(self, bus, lang):
        self.bus, self.lang = bus, lang

    @staticmethod
    def available(bus) -> bool:
        return bus.wait_for_response(Message("opm.tts.query"), timeout=1.5) is not None

    def __call__(self, text):
        r = self.bus.wait_for_response(Message("speak:b64_audio", {"utterance": text, "lang": self.lang}),
                                       "speak:b64_audio.response", timeout=30)
        if r is None or not r.data.get("audio"):
            raise RuntimeError("ovos-audio did not speak")
        return wav_samples(base64.b64decode(r.data["audio"]))


class PluginVoice:
    """The configured TTS plugin, loaded in the skill."""

    def __init__(self, lang, config=None):
        from ovos_config import Configuration
        from ovos_plugin_manager.tts import OVOSTTSFactory
        self.lang = lang
        cfg = dict(config or Configuration().get("tts", {}))
        self.engines = []
        for module in [cfg.get("module"), cfg.get("fallback_module")]:
            if not module:
                continue
            try:
                self.engines.append(OVOSTTSFactory.create({"module": module, module: cfg.get(module, {})}))
            except Exception:
                continue
        if not self.engines:
            raise RuntimeError("no TTS plugin could be loaded")

    def __call__(self, text):
        err = None
        for tts in self.engines:
            try:
                with tempfile.NamedTemporaryFile(suffix="." + getattr(tts, "audio_ext", "wav")) as f:
                    path, _ = tts.get_tts(text, f.name, lang=self.lang)
                    with open(path, "rb") as fh:
                        return wav_samples(fh.read())
            except Exception as e:  # the next engine (fallback) tries
                err = e
        raise RuntimeError(f"TTS failed: {err}")


def device_voice(bus, lang):
    if BusVoice.available(bus):
        return BusVoice(bus, lang)
    return PluginVoice(lang)


def voice_id(lang) -> str:
    """Which voice sings - part of the cache key, so a new voice sings anew."""
    from ovos_config import Configuration
    cfg = Configuration().get("tts", {})
    module = cfg.get("module", "")
    return f"{module}:{cfg.get(module, {}).get('voice', '')}:{lang}"
