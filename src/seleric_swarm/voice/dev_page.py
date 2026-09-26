"""A minimal same-origin join page for the Phase 0 transport proof.

Served from the API rather than ``office-ui`` on purpose: the API's CORS policy
allows GET only, so a page on the Vite dev port could not POST for a token.
Same-origin sidesteps that entirely for what is a throwaway dev affordance.

Gated on ``voice_enabled`` **and** a dev app_env — this must never be reachable
from a production deployment.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from seleric_swarm.voice.token import router

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seleric Voice — dev console</title>
<style>
  :root { color-scheme: dark; --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e;
          --accent:#2f81f7; --ok:#3fb950; --err:#f85149; --panel:#161b22; }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.5
         ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
         display:flex; justify-content:center; padding:32px 16px; }
  main { width:100%; max-width:640px; }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { color:var(--muted); margin:0 0 24px; font-size:14px; }
  button { background:var(--accent); color:#fff; border:0; border-radius:6px;
           padding:10px 18px; font-size:15px; cursor:pointer; }
  button[disabled] { opacity:.5; cursor:not-allowed; }
  button.muted { background:var(--err); }
  #muteBtn { margin-left:8px; }
  label { display:block; font-size:13px; color:var(--muted); margin:16px 0 4px; }
  input { width:100%; box-sizing:border-box; background:var(--panel);
          border:1px solid #30363d; border-radius:6px; color:var(--fg);
          padding:8px 10px; font:inherit; font-size:14px; }
  #status { margin:20px 0; padding:16px; border-radius:8px;
            background:var(--panel); font-size:16px; font-weight:500;
            display:flex; align-items:center; gap:12px; border:1px solid #30363d; }
  .badge { display:inline-block; width:12px; height:12px; border-radius:50%; background:#8b949e; }
  .badge.listening { background:var(--ok); box-shadow: 0 0 10px var(--ok); animation: pulse 1.5s infinite; }
  .badge.speaking { background:var(--accent); box-shadow: 0 0 10px var(--accent); animation: pulse 1s infinite; }
  .badge.err { background:var(--err); }
  @keyframes pulse { 0% { opacity:0.5; } 50% { opacity:1; } 100% { opacity:0.5; } }
  .ok { color:var(--ok); } .err { color:var(--err); }
  #log { background:var(--panel); border-radius:6px; padding:12px 14px;
         font:13px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
         white-space:pre-wrap; word-break:break-word; max-height:320px;
         overflow:auto; color:var(--muted); }
</style>
</head>
<body>
<main>
  <h1>Seleric Voice — dev console</h1>
  <p class="sub">Talk to the voice agent. Business questions are sent to Seleric as real missions; small talk is answered directly.</p>

  <label for="key">API key (<code>API_KEY</code> from your .env)</label>
  <input id="key" type="password" placeholder="required" autocomplete="off">

  <div id="status"><span id="badge" class="badge"></span><span id="statusText">Idle.</span></div>
  <button id="go">Connect and talk</button>
  <button id="muteBtn" disabled>Mute mic</button>

  <label style="margin-top:16px;">Microphone Input Level (VU Meter)</label>
  <div style="background:var(--panel); border:1px solid #30363d; border-radius:6px; height:10px; width:100%; overflow:hidden; margin-bottom:16px;">
    <div id="micMeter" style="background:var(--ok); height:100%; width:0%; transition:width 0.08s ease-out; box-shadow: 0 0 8px var(--ok);"></div>
  </div>

  <label>Live Conversation & Transcription</label>
  <div id="transcriptBox"><span style="color:var(--muted)">Connect and speak to see live speech transcriptions here…</span></div>

  <label>Log</label>
  <div id="log"></div>
</main>

<script type="module">
import { Room, RoomEvent } from
  'https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.esm.mjs';

const statusText = document.getElementById('statusText');
const badge = document.getElementById('badge');
const transcriptBox = document.getElementById('transcriptBox');
const micMeter = document.getElementById('micMeter');
const logEl = document.getElementById('log');
const btn = document.getElementById('go');
const muteBtn = document.getElementById('muteBtn');
let activeRoom = null;
let meterStream = null;
let micOn = true;

const log = (m) => {
  logEl.textContent += `${new Date().toLocaleTimeString()}  ${m}\n`;
  logEl.scrollTop = logEl.scrollHeight;
};
const setMicOn = async (on) => {
  if (!activeRoom) return;
  await activeRoom.localParticipant.setMicrophoneEnabled(on);
  if (meterStream) meterStream.getAudioTracks().forEach((t) => { t.enabled = on; });
  micOn = on;
  muteBtn.textContent = on ? 'Mute mic' : 'Unmute mic';
  muteBtn.classList.toggle('muted', !on);
  if (!on) micMeter.style.width = '0%';
  log(on ? 'mic unmuted' : 'mic muted');
};
muteBtn.addEventListener('click', () => setMicOn(!micOn).catch((e) => log(`mute error ${e}`)));

const setStatus = (m, state = '') => {
  statusText.textContent = m;
  badge.className = 'badge ' + state;
};

let currentInterimEl = null;

const addTranscript = (speaker, text, isFinal = true) => {
  if (transcriptBox.querySelector('span')) transcriptBox.innerHTML = '';
  
  if (!isFinal) {
    if (!currentInterimEl) {
      currentInterimEl = document.createElement('div');
      currentInterimEl.style.margin = '4px 0';
      currentInterimEl.style.fontWeight = '500';
      transcriptBox.appendChild(currentInterimEl);
    }
    currentInterimEl.style.color = speaker === 'You' ? 'var(--ok)' : 'var(--accent)';
    currentInterimEl.style.opacity = '0.7';
    currentInterimEl.textContent = `${speaker}: ${text}`;
  } else {
    if (currentInterimEl) {
      currentInterimEl.remove();
      currentInterimEl = null;
    }
    const line = document.createElement('div');
    line.style.margin = '4px 0';
    line.style.fontWeight = '500';
    line.style.color = speaker === 'You' ? 'var(--ok)' : 'var(--accent)';
    line.textContent = `${speaker}: ${text}`;
    transcriptBox.appendChild(line);
  }
  transcriptBox.scrollTop = transcriptBox.scrollHeight;
};

btn.addEventListener('click', async () => {
  const key = document.getElementById('key').value.trim();
  if (!key) { setStatus('Enter your API key first.', 'err'); return; }
  btn.disabled = true;
  try {
    setStatus('Requesting a room token…');
    const res = await fetch('/v1/voice/token', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': key },
      body: JSON.stringify({ title: 'Voice dev session' }),
    });
    if (!res.ok) {
      throw new Error(`token request failed: ${res.status} ${await res.text()}`);
    }
    const { url, token, room, thread_id } = await res.json();
    log(`token ok — room=${room}`);
    log(`thread=${thread_id}`);

    const rtcRoom = new Room({ adaptiveStream: true, dynacast: true });
    rtcRoom
      .on(RoomEvent.Connected, () => {
        setStatus('🎙️ Connected & Listening — Speak into your mic now!', 'listening');
        log('connected to room');
      })
      .on(RoomEvent.Disconnected, (r) => {
        setStatus(`Disconnected (${r ?? 'unknown'}).`, '');
        btn.disabled = false;
        muteBtn.disabled = true;
        activeRoom = null;
      })
      .on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const agentSpeaking = speakers.some(p => p.identity.startsWith('agent-'));
        const userSpeaking = speakers.some(p => !p.identity.startsWith('agent-'));
        if (agentSpeaking) {
          setStatus('🔊 Agent is speaking…', 'speaking');
        } else if (userSpeaking) {
          setStatus('🎙️ User is speaking…', 'listening');
        } else {
          setStatus('🎙️ Connected & Listening — Speak into your mic now!', 'listening');
        }
      })
      .on(RoomEvent.ParticipantConnected, (p) => log(`agent joined: ${p.identity}`))
      .on(RoomEvent.TrackSubscribed, (t) => {
        if (t.kind === 'audio') { t.attach(); log('agent audio attached'); }
      })
      .on(RoomEvent.DataReceived, (payload) => {
        try {
          const str = new TextDecoder().decode(payload);
          const data = JSON.parse(str);
          if (data.type === 'transcript') {
            addTranscript(data.speaker || 'You', data.text, data.is_final);
            if (data.is_final) {
              log(`${data.speaker || 'User'}: "${data.text}"`);
            }
          }
        } catch (e) {}
      });

    setStatus('Connecting to LiveKit…');
    await rtcRoom.connect(url, token);
    await rtcRoom.localParticipant.setMicrophoneEnabled(true);
    activeRoom = rtcRoom;
    micOn = true;
    muteBtn.disabled = false;
    muteBtn.textContent = 'Mute mic';
    muteBtn.classList.remove('muted');
    log('microphone published');

    // Live Web Audio VU Meter
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      meterStream = stream;
      stream.getAudioTracks().forEach((t) => { t.enabled = micOn; });
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);
      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateMeter = () => {
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
        const avg = sum / dataArray.length;
        if (micMeter) micMeter.style.width = Math.min(100, Math.max(0, avg * 3)) + '%';
        requestAnimationFrame(updateMeter);
      };
      updateMeter();
    } catch (micErr) {
      log('mic meter notice: ' + micErr);
    }
  } catch (err) {
    setStatus(String(err && err.message ? err.message : err), 'err');
    log(`ERROR ${err}`);
    btn.disabled = false;
  }
});
</script>
</body>
</html>
"""


@router.get("/dev", response_class=HTMLResponse, include_in_schema=False)
def voice_dev_page(request: Request) -> HTMLResponse:
    provider = getattr(request.app.state, "runtime_provider", None)
    settings = provider().settings if callable(provider) else None
    if settings is None or not getattr(settings, "voice_enabled", False):
        raise HTTPException(status_code=404, detail="voice is not enabled")
    is_dev = getattr(settings, "is_dev_surface", None)
    if not callable(is_dev) or not is_dev():
        raise HTTPException(status_code=404, detail="not available")
    return HTMLResponse(_PAGE)
