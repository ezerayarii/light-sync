"""Where the sound comes from: the mic list, laptop sound, Bluetooth from the phone."""

import json
import os
import sys
import threading
import time

from parts.config import BLOCK, RATE
from parts import web


# the sound sources, in plain words (Oct 19: the owner couldn't find the room mic among technical device names,
# and picked "Microphone (Steam Streaming Microphone)", a virtual device that hears nothing)
LOOPBACK = "Laptop sound"

AUTO_SOUND = "Auto (laptop sound or room mic)"   # laptop sound while it plays, else the camera mic via the Pi

ROOM_MIC = "Room microphone (camera)"

OLD_LABELS = {"Auto: laptop sound, else the room": AUTO_SOUND, "Laptop sound (what's playing)": LOOPBACK,
              "Room mic (camera, via the Pi)": ROOM_MIC}

NOT_MICS = ("steam streaming", "virtual", "stereo mix", "sound mapper", "primary sound capture", "what u hear")

PHONE_BT = "Phone (Bluetooth)"  # this laptop as a Bluetooth speaker for a phone paired with it (Oct 20, owner: music

#                                 from the phone, also without Wi-Fi)
BT = {"state": "off", "phone": None, "paired": []}  # off / starting / not paired / waiting / connected

# Windows' AudioPlaybackConnection (built in since Windows 10 2004, nothing to install), from PowerShell. Passed as
# -Command text (script files are blocked by the execution policy here), so: no double quotes in it. Prints one JSON
# line per change; ONCE=1 only lists the paired phones. Ends with Light Sync (PARENT_PID), and the connection with it:
# the phone plays on its own speaker again.
BT_SPEAKER_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$m = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$opT = $m | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$actT = $m | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Await($op, [Type]$t) { $k = $opT.MakeGenericMethod($t).Invoke($null, @($op)); if (-not $k.Wait(20000)) { throw 'timeout' }; $k.Result }
function Run($a) { $k = $actT.Invoke($null, @($a)); if (-not $k.Wait(20000)) { throw 'timeout' } }
$null = [Windows.Media.Audio.AudioPlaybackConnection, Windows.Media.Audio, ContentType = WindowsRuntime]
$null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
$sel = [Windows.Media.Audio.AudioPlaybackConnection]::GetDeviceSelector()
$conns = @{}
$script:last = ''
function Say($state, $phone, $names) {
  $j = [ordered]@{ state = $state; phone = $phone; paired = @($names) } | ConvertTo-Json -Compress
  if ($j -ne $script:last) { [Console]::Out.WriteLine($j); [Console]::Out.Flush(); $script:last = $j }
}
while (Get-Process -Id PARENT_PID -ErrorAction SilentlyContinue) {
  $devs = @(Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($sel)) ([Windows.Devices.Enumeration.DeviceInformationCollection]))
  $names = @($devs | ForEach-Object { $_.Name })
  if (ONCE) { Say 'listed' $null $names; break }
  $open = $null
  foreach ($d in $devs) {
    if (-not $conns[$d.Id]) {
      try { $c = [Windows.Media.Audio.AudioPlaybackConnection]::TryCreateFromId($d.Id); if ($c) { Run $c.StartAsync(); $conns[$d.Id] = $c } } catch {}
    }
    $c = $conns[$d.Id]
    if ($c -and $c.State -eq 'Opened') { $open = $d.Name; break }
  }
  if (-not $open) {
    foreach ($d in $devs) {
      $c = $conns[$d.Id]
      if (-not $c) { continue }
      try { $r = Await $c.OpenAsync() ([Windows.Media.Audio.AudioPlaybackConnectionOpenResult]); if ($r.Status -eq 'Success') { $open = $d.Name; break } } catch {}
    }
  }
  if ($open) { Say 'connected' $open $names } elseif ($devs.Count) { Say 'waiting' $null $names } else { Say 'not paired' $null $names }
  Start-Sleep -Seconds 4
}
"""

def bt_powershell(once=False):
    import subprocess
    return subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                             BT_SPEAKER_PS.replace("PARENT_PID", str(os.getpid())).replace("ONCE", "1" if once else "0")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

def bt_paired():
    """The phones paired with this laptop that can play to it (a quick look, for the sound test)."""
    try:
        p = bt_powershell(once=True)
        out, _ = p.communicate(timeout=25)
        return json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1]).get("paired") or []
    except Exception:
        return None

def bt_label():
    s, p = BT.get("state"), BT.get("phone")
    return (f"phone over Bluetooth: {p}" if s == "connected" else
            "Phone (Bluetooth): no phone paired with the laptop yet" if s == "not paired" else
            "Phone (Bluetooth): waiting for the phone (Bluetooth on, near the laptop)" if s == "waiting" else
            "Phone (Bluetooth): starting")

def mic_name(name):
    """A plain name for a real microphone, or None for virtual devices that hear nothing."""
    n = name.lower()
    if any(k in n for k in NOT_MICS):
        return None
    if "array" in n or ("intel" in n and "microphone" in n):
        return "Laptop microphone"
    if "g733" in n:
        return "Headset microphone (G733)"
    if "headset" in n:
        return "Headset microphone"
    return name

class NoMic:
    """"Room mic": nothing to open here, the sound comes from the Pi (POST /room)."""

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

class LoopbackStream:
    """What the laptop is playing (Windows loopback via the 'soundcard' package), fed to the Ear like a mic.
    Follows the current default output (speakers or headset), so it works with headphones on."""

    def __init__(self, ear):
        import soundcard  # import on the main thread: it sets up COM there itself
        self.sc, self.ear, self.running = soundcard, ear, True
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.running = False

    def close(self):
        self.thread.join(timeout=2)

    def run(self):
        import ctypes
        import warnings
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # this reader thread needs COM too
        sc = self.sc
        warnings.filterwarnings("ignore", module="soundcard")  # "data discontinuity" chatter
        while self.running:
            try:
                spk = sc.default_speaker()
                with sc.get_microphone(spk.id, include_loopback=True).recorder(samplerate=RATE * 2, channels=1) as rec:
                    checked = time.time()
                    while self.running:
                        data = rec.record(numframes=BLOCK * 2)[:, 0]  # 44.1 kHz -> average pairs -> 22.05 kHz
                        if len(data) == BLOCK * 2:
                            self.ear.process(data.reshape(BLOCK, 2).mean(axis=1), time.time())
                        if time.time() - checked > 2:  # output switched (headset <-> speakers)?
                            checked = time.time()
                            if sc.default_speaker().id != spk.id:
                                break
            except Exception as e:
                print(f"Laptop sound: {e} - retrying", flush=True)
                time.sleep(2)

class BluetoothSpeaker(LoopbackStream):
    """"Phone (Bluetooth)": the laptop is a Bluetooth speaker for a phone paired with it (BT_SPEAKER_PS keeps the
    connection open). The phone's music plays from the laptop's speakers and Light Sync hears it exactly, like any
    laptop sound; no Wi-Fi needed. Picking another source ends the connection."""

    def __init__(self, ear):
        super().__init__(ear)
        self.proc = None

    def start(self):
        super().start()
        BT.update(state="starting", phone=None)
        threading.Thread(target=self.helper, daemon=True).start()

    def stop(self):
        super().stop()
        if self.proc:
            self.proc.kill()

    def helper(self):
        while self.running:
            try:
                self.proc = bt_powershell()
                for line in self.proc.stdout:
                    try:
                        st = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if (st.get("state"), st.get("phone")) != (BT.get("state"), BT.get("phone")):
                        print(f"Phone (Bluetooth): {st.get('state')}{' - ' + st['phone'] if st.get('phone') else ''}", flush=True)
                    BT.update(st)
                self.proc.wait()
            except Exception as e:
                print(f"Phone (Bluetooth): {e}", flush=True)
            if self.running:
                time.sleep(5)
        BT.update(state="off", phone=None)

def set_audio(label):
    """The audio source shown on the dashboard (a listening phone is shown on top of it while it sends)."""
    web.STATE["audio"] = web.STATE["audio_local"] = label

def list_mics():
    """Microphones to choose from, as {label: device index}. On Windows only MME devices
    (they resample to our rate); MME cuts names at 31 characters, so use the full name."""
    import sounddevice as sd
    devs, apis = sd.query_devices(), sd.query_hostapis()
    mme = any(a["name"] == "MME" for a in apis)
    full = [d["name"] for d in devs if d["max_input_channels"] > 0]
    mics = {}
    import importlib.util
    if sys.platform == "win32" and importlib.util.find_spec("soundcard"):
        mics[AUTO_SOUND] = "auto"  # first: a reset picks it
        mics[LOOPBACK] = "loopback"
        mics[PHONE_BT] = "phone_bt"
    mics[ROOM_MIC] = "room"
    for i, d in enumerate(devs):
        if d["max_input_channels"] <= 0 or "Sound Mapper" in d["name"]:
            continue
        if mme and apis[d["hostapi"]]["name"] != "MME":
            continue
        label = mic_name(next((n for n in full if n.startswith(d["name"]) and len(n) > len(d["name"])), d["name"]))
        if label:
            mics.setdefault(label, i)
    return mics

def open_mic(sd, ear, mics, label, fallback):
    """Open the mic picked on the dashboard; if it fails, the default one."""
    kind = mics.get(label)
    clean = kind in ("loopback", "auto", "phone_bt")  # the laptop's own sound (a phone over Bluetooth plays there too)
    ear.gate = 0.0005 if clean else 0.003  # laptop sound has no room noise: hear quiet music
    # a mic: mic beats (see Ear); a phone over Bluetooth too: any kind of music (Oct 20, the owner's songs: the kick
    # detector locked 4 % of the time, mic beats 59 %)
    ear.detector = "bass" if kind in ("loopback", "auto") else "broad"
    ear.room_mode = {"auto": "auto", "room": "only"}.get(kind)    # the Pi sends the room's sound (see room_wanted)
    try:
        if kind == "phone_bt":
            mic = BluetoothSpeaker(ear)
        elif kind in ("loopback", "auto"):
            mic = LoopbackStream(ear)
        elif kind == "room":
            mic = NoMic()
        else:
            mic = sd.InputStream(device=mics.get(label, fallback), channels=1, samplerate=RATE,
                                 blocksize=BLOCK, callback=ear.callback)
        mic.start()
    except Exception as e:
        print(f"Mic '{label}' failed ({e}) - using the default mic", flush=True)
        label, ear.room_mode, ear.gate, ear.detector = "Windows default", None, 0.003, "broad"
        mic = sd.InputStream(device=fallback, channels=1, samplerate=RATE, blocksize=BLOCK, callback=ear.callback)
        mic.start()
    set_audio(f"mic: {label}")
    print(f"Audio: {label}", flush=True)
    return mic, label
