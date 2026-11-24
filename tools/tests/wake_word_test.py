#!/usr/bin/env python3
"""
wake_word_test.py - tests the voice pipeline's wake word with a computer voice, without the microphone and without
anything being switched (Oct 27, owner: "rocky is the name, no Nabu"; picked "Hey Rick", a word someone already trained,
to see whether it also wakes up for "Hey Rocky").

Each sentence is spoken by Home Assistant's own Piper, then sent to the pipeline's wake-word step only
(assist_pipeline/run, wake_word to wake_word): it says whether the word was heard, nothing else runs.

  py tools/tests/wake_word_test.py               # the sentences below, with the pipeline's wake word
  py tools/tests/wake_word_test.py "Hey Rocky"   # one sentence
"""
import audioop
import io
import os
import sys
import time
import wave

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
PIPELINE = "01kt36qed9zgnfxh9qe0zy89gc"  # "Home Assistant", the one the MeetUp uses
RATE = 16000
# (sentence, should it wake?)  None = no opinion (close to the word, either is fine)
CASES = [
    ("Hey Rick.", True),
    ("Hey Rick!", True),
    ("Hey Rocky.", True),
    ("Hey Rocky!", True),
    ("Hey Rocky, put on a movie.", True),
    ("Hey Ricky.", None),
    ("Rocky.", None),
    ("Okay Nabu.", False),
    ("Hello there.", False),
    ("Hey, how are you?", False),
    ("Turn on the lights.", False),
    ("Welcome back to the show. Tonight we have the weather, the football scores and a new recipe for chicken "
     "soup, so stay with us after the break.", False),
]


def speak(text, pipe):
    """The sentence as 16 kHz mono 16-bit audio, from the pipeline's own Piper voice."""
    r = requests.post(f"{HA}/api/tts_get_url", headers=H, timeout=60, json={
        "engine_id": pipe["tts_engine"], "message": text, "language": pipe["tts_language"],
        "options": {"voice": pipe["tts_voice"], "preferred_format": "wav", "preferred_sample_rate": RATE,
                    "preferred_sample_channels": 1, "preferred_sample_bytes": 2}})
    r.raise_for_status()
    wav = requests.get(r.json()["url"].replace("http://homeassistant.local:8123", HA), timeout=60).content
    with wave.open(io.BytesIO(wav)) as w:
        pcm, rate, ch, width = w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getsampwidth()
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if ch != 1:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, RATE, None)
    return pcm


def heard(ws, pcm):
    """Sends the audio to the wake-word step: the word it heard, or None."""
    silence = b"\0\0" * RATE
    audio = silence * 2 + pcm + silence * 2
    rid = ws.next_id
    ws.next_id += 1
    ws.send({"id": rid, "type": "assist_pipeline/run", "start_stage": "wake_word", "end_stage": "wake_word",
             "pipeline": PIPELINE, "input": {"sample_rate": RATE, "timeout": len(audio) // (2 * RATE) + 2,
                                             "noise_suppression_level": 0, "auto_gain_dbfs": 0, "volume_multiplier": 1.0}})

    def mine():  # a finished run still sends a late reply for its audio: only this run's messages count
        while True:
            m = ws.recv()
            if m.get("id") == rid:
                return m

    handler = None
    while handler is None:
        m = mine()
        if m.get("type") == "result" and not m.get("success"):
            raise RuntimeError(m.get("error"))
        ev = m.get("event") or {}
        if ev.get("type") == "run-start":
            handler = ev["data"]["runner_data"]["stt_binary_handler_id"]
    step = RATE // 10 * 2  # 0.1 s, sent at twice the real pace
    for i in range(0, len(audio), step):
        ws.send_bytes(bytes([handler]) + audio[i:i + step])
        time.sleep(0.05)
    ws.send_bytes(bytes([handler]))
    word = None
    while True:
        ev = (mine().get("event") or {})
        if ev.get("type") == "wake_word-end":
            word = ((ev.get("data") or {}).get("wake_word_output") or {}).get("wake_word_id")
        if ev.get("type") in ("run-end", "error"):
            return word


def main():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    pipe = ws.call(type="assist_pipeline/pipeline/get", pipeline_id=PIPELINE)
    print(f"Wake word: {pipe['wake_word_id']} ({pipe['wake_word_entity']}), voice {pipe['tts_voice']}\n")
    cases = [(" ".join(sys.argv[1:]), None)] if sys.argv[1:] else CASES
    right = judged = 0
    for text, should in cases:
        pcm = speak(text, pipe)
        results = [heard(ws, pcm), heard(ws, audioop.mul(pcm, 2, 0.4))]  # normal and quieter (across the room)
        time.sleep(2.5)  # Home Assistant ignores the same wake word twice within 2 s
        woke = [bool(r) for r in results]
        mark = ""
        if should is not None:
            judged += 2
            right += sum(w == should for w in woke)
            mark = "ok" if all(w == should for w in woke) else "WRONG"
        print(f"{mark:5s}  {text[:60]:60s}  normal: {'woke' if woke[0] else '-':5s}  quiet: {'woke' if woke[1] else '-'}")
    if judged:
        print(f"\n{right}/{judged} as expected")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
