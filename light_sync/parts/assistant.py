"""Rocky's doorway to the local AI: what he may do, and repairing what the model gets wrong."""

import json
import os
import re
import requests
import tempfile

from parts.config import ROLE
from parts import config, pi_backup, app_requests, web


# Home Assistant's voice assistant (Oct 27, owner: a smarter "Okay Nabu"): HA's Ollama integration on the Pi uses this
# laptop's local model through the show port. Only from the Pi, and only what a conversation needs: the models' list
# and chat. Ollama itself stays on 127.0.0.1, and nothing here can pull, change or delete a model.
OLLAMA_PROXY = {("GET", "/api/tags"), ("GET", "/api/version"), ("POST", "/api/show"), ("POST", "/api/chat")}

# Testing the assistant without it doing anything (Oct 27: asked "what can you do?", llama3.2 3B called All off):
# while this file exists, the model's tool calls are written to ASSIST_TEST_LOG instead of being passed on, so Home
# Assistant only gets the words. tools/tests/assistant_test.py switches it on and off.
ASSIST_TEST_FLAG = os.path.join(tempfile.gettempdir(), "lightsync_assistant_test")

ASSIST_TEST_LOG = ASSIST_TEST_FLAG + ".jsonl"

# The guard (Oct 27, owner picked it over a bigger model): the small model acted on chit-chat ("hello" -> All off,
# "thanks" -> Good night, "is the TV on?" -> pause). Unless the sentence clearly asks for a change, the model only gets
# the tools that read (so it can still answer "which lights are on?"), never the ones that switch anything.
ASSIST_READ = ("GetLiveContext", "GetDateTime", "HassGetState", "HassGetWeather", "todo_get_items")

ASSIST_QUESTION = re.compile(r"^(please |hey |ok |okay )*(what|which|how|is|are|was|were|does|do|did|why|when|who|where|"
                             r"whats|what's|hows|how's|tell me|can you tell|could you tell|do you know)\b")

ASSIST_POLITE = re.compile(r"^(please |hey |ok |okay )*(can|could|would|will) you (please )?(turn|switch|set|dim|brighten|"
                           r"make|start|play|pause|stop|put|change|mute|lower|raise|resume)\b")

ASSIST_ACT = set("""turn switch set dim brighten brighter darker bright dark lower raise make start play pause stop resume put
    change mute unmute skip louder quieter volume off on up down percent warmer cooler warm cool party movie film game gaming
    sleep bed night normal cozy cosy relax reading colour color red orange yellow green blue purple pink white""".split())

def assist_may_act(text):
    """Does this sentence clearly ask for a change? (questions, greetings, thanks, jokes: no)"""
    t = re.sub(r"\s+", " ", text.lower()).strip(" .!")
    if ASSIST_POLITE.match(t):
        return True
    if ASSIST_QUESTION.match(t) or t.endswith("?"):
        return False
    return bool(ASSIST_ACT & set(re.findall(r"[a-z]+", t)))

ASSIST_HOME = set("""light lights lamp tv television corner top tvd strip room mode modes music song playing bright brightness
    dark colour color on off volume show movie game party""".split())

ASSIST_YES = re.compile(r"^(yes|yeah|yep|yup|sure|ok|okay|please|do it|go ahead|go for it|sounds good|why not|"
                        r"let's do it|lets do it)\b")

def assist_guard(body):
    """The chat request with what the user's last sentence needs: a change -> every tool; a question about the home
    -> only the reading tools; anything else (hello, thanks, a joke, "what can you do?") -> no tools, just words.
    A yes right after the assistant's own question ("Want me to start Movie time?" "yes please") counts as asking."""
    try:
        req = json.loads(body)
    except ValueError:
        return body, ""
    msgs = req.get("messages", [])
    asked = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"), "")
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    offered = next((m.get("content", "") for m in reversed(msgs[:last_user]) if m.get("role") == "assistant"), "")
    # a yes counts after one offer ("Want me to start Movie time?"), not after a choice ("a movie, a game or some
    # music?": which one? the model then picked one at random)
    agreed = bool(offered.strip().endswith("?") and " or " not in offered.lower()
                  and ASSIST_YES.match((asked or "").lower().strip()))
    if not req.get("tools") or assist_may_act(asked or "") or agreed:
        return body, asked
    words = set(re.findall(r"[a-z]+", (asked or "").lower()))
    if words & ASSIST_HOME:
        req["tools"] = [t for t in req["tools"] if (t.get("function") or {}).get("name", "").split("__")[-1] in ASSIST_READ]
    else:
        req.pop("tools")
    return json.dumps(req).encode(), asked

def assist_clean_args(args):
    """Arguments the small model makes up that make Home Assistant find nothing or do the wrong thing (Oct 27: name
    "XBR-65X850D" with area and floor "Salon" for the bedroom; brightness 0 next to "blue", which switches it off)."""
    a = {k: v for k, v in args.items() if v not in ("", None)}  # "color": "" makes Home Assistant refuse the call
    a.pop("floor", None)  # this home has no floors
    if a.get("name"):
        a.pop("area", None)  # the name alone finds it; a wrong room made it fail
    if str(a.get("brightness", "")).strip() in ("0", "0.0") and (a.get("color") or a.get("temperature")):
        a.pop("brightness")
    return a

def assist_repair(lines, offered):
    """The whole answer (Ollama's stream lines), repaired. The 3B model sometimes writes its action out as text,
    mostly after a failed one ({"name": "light__HassLightSet", "parameters": {...}}): the owner got that JSON as the
    answer on the phone (Oct 27). Written out, it becomes a real call when it's one of the tools the guard offered,
    and is dropped otherwise; every call's arguments are cleaned (assist_clean_args). An answer with no call passes
    as it was."""
    try:
        msgs = [json.loads(line) for line in lines]
    except ValueError:
        return lines
    calls, text = [], ""
    for m in msgs:
        calls += (m.get("message") or {}).get("tool_calls") or []
        text += (m.get("message") or {}).get("content") or ""
    written, broken, dec, i = [], False, json.JSONDecoder(), 0
    while (i := text.find("{", i)) >= 0:
        obj = None
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            if not re.match(r'\{\s*"name"\s*:', text[i:]):
                i += 1
                continue
            for tail in ("}", "}}", "}}}"):  # written out but broken (Oct 27: a brace missing, or cut short)
                try:
                    obj, end = dec.raw_decode(text[i:] + tail)[0], len(text)
                    break
                except ValueError:
                    pass
            if obj is None:  # beyond repair: never shown as the answer
                broken, text = True, text[:i]
                break
        args = obj.get("parameters", obj.get("arguments")) if isinstance(obj, dict) else None
        if isinstance(args, dict) and isinstance(obj.get("name"), str):
            written.append({"function": {"name": obj["name"], "arguments": args}})
            text = text[:i] + text[end:]
        else:
            i = end
    if not calls and not written and not broken:
        return lines
    calls += [c for c in written if c["function"]["name"] in offered]
    calls = [dict(c, function=dict(c["function"], arguments=assist_clean_args(c["function"].get("arguments") or {})))
             for c in calls]
    sorry = "Sorry, I couldn't do that."
    msg = {"role": "assistant", "content": "" if calls else sorry if broken else (text.strip() or sorry)}
    if calls:
        msg["tool_calls"] = calls
    if written:
        print(f"Assistant: {len(written)} action(s) written out as text -> "
              f"{', '.join(c['function']['name'] for c in calls) or 'dropped'}", flush=True)
    if len(msgs) == 1:  # not streamed: one answer
        return [json.dumps(dict(msgs[0], message=msg)).encode()]
    return [json.dumps(dict(msgs[0], message=msg, done=False)).encode(),
            json.dumps(dict(msgs[-1], message={"role": "assistant", "content": ""})).encode()]

def _dry_line(line, asked):
    """One line of the model's answer in test mode: its tool calls kept aside, the rest passed on."""
    try:
        msg = json.loads(line)
    except ValueError:
        return line
    calls = (msg.get("message") or {}).pop("tool_calls", None)
    if calls:
        with open(ASSIST_TEST_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"asked": asked, "calls": calls}) + "\n")
    return json.dumps(msg).encode()

def ollama_forward(handler, method, path, body):
    """The Pi: Home Assistant's AI requests passed on to the laptop, wherever it is now (Oct 30: the router gave the
    laptop a new address, and the Ollama integration can't change its URL, so Rocky's AI failed). Home Assistant on this
    Pi only (the show port is open to the home network). The laptop checks them as ever: the guard, the repair."""
    url = config.MAIN_INFO.get("show_url")
    if handler.client_address[0] not in ("127.0.0.1", "::1"):
        handler.send_response(404)
        handler.end_headers()
        return
    # Nov 7: the Pi always drives now and the laptop only runs the game helper, which also passes Rocky's AI on
    # (game_helper.py, port 8090): the laptop's address is GAME_FROM. Off or asleep: no AI, the fixed commands still work
    base = url.rsplit("/show", 1)[0] if config.STANDBY and url else (f"http://{config.laptop_ip()}:8090" if config.laptop_ip() else "")
    if not base:
        handler.send_response(503)
        handler.end_headers()
        return
    try:
        r = pi_backup.PLAIN.request(method, base + "/ollama" + path, data=body, timeout=(5, 300), stream=True,
                          headers={"Content-Type": "application/json"} if body is not None else {})
    except requests.RequestException:
        handler.send_response(502)
        handler.end_headers()
        return
    try:
        handler.send_response(r.status_code)
        handler.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
        handler.end_headers()
        for chunk in r.iter_content(None):
            if chunk:
                handler.wfile.write(chunk)
                handler.wfile.flush()
    finally:
        r.close()

def ollama_proxy(handler, method, body=None):
    path = handler.path.split("?", 1)[0][len("/ollama"):]
    if ROLE != "main" and (method, path) in OLLAMA_PROXY:
        ollama_forward(handler, method, path, body)
        return
    if (method, path) not in OLLAMA_PROXY or not web.phone_allowed(handler.client_address[0]):
        handler.send_response(404)
        handler.end_headers()
        return
    asked = ""
    if path == "/api/chat":
        body, asked = assist_guard(body)
    try:
        r = requests.request(method, app_requests.OLLAMA_URL + path, data=body, timeout=(5, 300), stream=True,
                             headers={"Content-Type": "application/json"} if body is not None else {})
    except requests.RequestException:
        handler.send_response(502)
        handler.end_headers()
        return
    dry = path == "/api/chat" and os.path.exists(ASSIST_TEST_FLAG)
    try:
        handler.send_response(r.status_code)
        handler.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
        handler.end_headers()
        if path == "/api/chat" and r.status_code == 200:  # the whole answer first (~1 s), then repaired
            try:
                offered = {(t.get("function") or {}).get("name") for t in json.loads(body).get("tools") or []}
            except (ValueError, TypeError, AttributeError):
                offered = set()
            for line in assist_repair([line for line in r.iter_lines() if line], offered):
                handler.wfile.write((_dry_line(line, asked) if dry else line) + b"\n")  # one JSON message a line
            handler.wfile.flush()
            return
        for chunk in r.iter_content(None):  # a streamed answer passes through as it comes
            if chunk:
                handler.wfile.write(chunk)
                handler.wfile.flush()
    finally:
        r.close()
