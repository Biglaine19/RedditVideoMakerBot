"""Turns a story text file into a polished vertical TikTok video.

- Narration: Kokoro neural TTS (runs locally, no API key)
- Captions: word-by-word highlight, timed with faster-whisper
- Background: an original "escape the rings" physics animation, rendered frame by frame
- Audio: generated lo-fi music bed + bounce plucks, loudness-normalised to TikTok's -14 LUFS

Story file format: first line is the hook (shown on a title card), the rest is the body.

    python tiktok/make_story_video.py tiktok/stories/01-approval-emails.txt [more files...]

Setup (one time):
    pip install kokoro-onnx soundfile faster-whisper pycairo pillow numpy
    download kokoro-v1.0.onnx and voices-v1.0.bin from https://huggingface.co/fastrtc/kokoro-onnx
    into tiktok/models/ (or point KOKORO_DIR at them)
Videos land in tiktok/videos/.
"""
import colorsys
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cairo
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
MODEL_DIR = Path(os.environ.get("KOKORO_DIR", HERE / "models"))
OUT_DIR = HERE / "videos"
HANDLE = "@nightowlstories"
CHANNEL = "Night Owl Stories"
FONT_BOLD = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"

W, H, FPS = 1080, 1920, 30
SR = 48000
VOICE_SPEED = 1.12
SENTENCE_PAUSE = 0.12
TITLE_PAUSE = 0.35
WORDS_PER_CAPTION = 3
MAX_CAPTION_CHARS = 15  # keeps a chunk on one line inside the frame
RING_CENTER_Y = 780
CAPTION_Y = 1420


# ---------------------------------------------------------------- narration

def load_models():
    from faster_whisper import WhisperModel
    from kokoro_onnx import Kokoro

    tts = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
    asr = WhisperModel("base.en", device="cpu", compute_type="int8", download_root=str(MODEL_DIR / "whisper"))
    return tts, asr


def resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    positions = np.arange(0, len(audio), sr_in / sr_out)
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)


def word_times(asr, audio: np.ndarray, sr: int, sentence: str) -> list:
    """Start/end of every word in `sentence`, relative to the clip. Uses whisper's timings when it
    heard the same number of words as the script; otherwise spreads the words by length."""
    words = sentence.split()
    segments, _ = asr.transcribe(resample(audio, sr, 16000), word_timestamps=True, language="en")
    heard = [w for s in segments for w in s.words]
    if len(heard) == len(words):
        return [(word, w.start, w.end) for word, w in zip(words, heard)]
    clip, weights = len(audio) / sr, [len(w) + 2 for w in words]
    t, out = 0.0, []
    for word, weight in zip(words, weights):
        step = clip * weight / sum(weights)
        out.append((word, t, t + step))
        t += step
    return out


def narrate(tts, asr, sentences: list, voice: str):
    """Returns the full narration (48 kHz mono) and [(word, start, end, sentence_index)]."""
    pieces, words, t = [], [], 0.0
    for i, sentence in enumerate(sentences):
        audio, sr = tts.create(sentence, voice=voice, speed=VOICE_SPEED, lang="en-us")
        words += [(w, t + s, t + e, i) for w, s, e in word_times(asr, audio, sr, sentence)]
        clip = resample(audio, sr, SR)
        pause = TITLE_PAUSE if i == 0 else SENTENCE_PAUSE
        pieces += [clip, np.zeros(int(pause * SR), np.float32)]
        t += len(clip) / SR + pause
    return np.concatenate(pieces), words


# ---------------------------------------------------------------- music + sfx

def note(freq: float, length: float, decay: float, harmonics=(1.0, 0.25, 0.08)) -> np.ndarray:
    t = np.arange(int(length * SR)) / SR
    tone = sum(a * np.sin(2 * np.pi * freq * (k + 1) * t) for k, a in enumerate(harmonics))
    attack = np.minimum(t / 0.015, 1)
    return (tone * attack * np.exp(-t / decay)).astype(np.float32)


def soften(x: np.ndarray, cutoff: float) -> np.ndarray:
    """Cheap low-pass: two passes of a moving average."""
    k = np.ones(max(1, int(SR / cutoff))) / max(1, int(SR / cutoff))
    return np.convolve(np.convolve(x, k, "same"), k, "same").astype(np.float32)


def lofi_bed(length: float, rng: np.random.Generator) -> np.ndarray:
    """A warm 4-chord lo-fi loop: electric-piano chords, sine bass, soft kick/hat, vinyl crackle."""
    midi = lambda m: 440 * 2 ** ((m - 69) / 12)
    chords = [[57, 60, 64, 67], [53, 57, 60, 64], [48, 52, 55, 59], [55, 59, 62, 66]]  # Am7 Fmaj7 Cmaj7 G
    bar = 60 / 76 * 4
    out = np.zeros(int((length + 2 * bar) * SR), np.float32)

    def add(sound, at):
        i = int(at * SR)
        out[i : i + len(sound)] += sound[: max(0, len(out) - i)]

    t, n = 0.0, 0
    while t < length:
        chord = chords[n % len(chords)]
        for k, m in enumerate(chord):
            add(0.09 * note(midi(m + 12), bar, 1.4), t + k * 0.03)
            add(0.05 * note(midi(m + 12), bar / 2, 0.6), t + bar / 2 + k * 0.03)
        add(0.22 * note(midi(chord[0] - 12), bar, 1.8, (1.0, 0.1)), t)
        for beat in range(4):
            bt = t + beat * bar / 4
            if beat in (0, 2):
                kt = np.arange(int(0.25 * SR)) / SR
                add((0.35 * np.sin(2 * np.pi * (50 + 90 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 14)).astype(np.float32), bt)
            for off in (0, bar / 8):
                hat = rng.standard_normal(int(0.04 * SR)).astype(np.float32)
                add(0.025 * hat * np.exp(-np.arange(len(hat)) / SR * 90), bt + off)
        t += bar
        n += 1
    out = soften(out, 3000)
    crackle = (rng.random(len(out)) < 0.0004) * rng.standard_normal(len(out)) * 0.05
    return (out + crackle.astype(np.float32))[: int(length * SR)]


def sfx_track(events: list, length: float) -> np.ndarray:
    """Soft plucks on every bounce (climbing a pentatonic scale) and a chime on every ring break."""
    out = np.zeros(int((length + 1) * SR), np.float32)
    scale = [0, 3, 5, 7, 10]
    for count, (t, kind) in enumerate(events):
        if kind == "bounce":
            step = scale[count % 5] + 12 * ((count // 5) % 2)
            sound = 0.14 * note(523.25 * 2 ** (step / 12), 0.3, 0.08)
        else:
            sound = sum(0.1 * note(f, 0.9, 0.35) for f in (1046.5, 1318.5, 1568.0))
        i = int(t * SR)
        out[i : i + len(sound)] += sound[: max(0, len(out) - i)]
    return out[: int(length * SR)]


# ---------------------------------------------------------------- background animation

class RingEscape:
    """Ball bouncing inside rotating rings; slipping through a ring's gap shatters it."""

    CENTER = np.array([W / 2, RING_CENTER_Y], float)
    BALL_R = 20
    LINE_W = 14
    GAP = 0.62
    GRAVITY = 1300

    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)
        self.hue = self.rng.random()
        self.events, self.particles, self.trail = [], [], []
        self.t = 0.0
        self.new_round()

    def new_round(self):
        self.hue = (self.hue + 0.37) % 1
        self.rings = [
            {"r": 190 + 48 * i, "rot": self.rng.random() * math.tau,
             "w": (0.9 + 0.18 * i) * (1 if i % 2 else -1), "hue": (self.hue + 0.07 * i) % 1}
            for i in range(6)
        ]
        self.pos = self.CENTER.copy()
        ang = self.rng.random() * math.tau
        self.vel = np.array([math.cos(ang), math.sin(ang)]) * 600
        self.escaped_at = None
        self.trail = []

    def burst(self, ring):
        for _ in range(70):
            a = self.rng.random() * math.tau
            direction = np.array([math.cos(a), math.sin(a)])
            self.particles.append([self.CENTER + ring["r"] * direction,
                                   direction * self.rng.uniform(150, 650), 1.0, ring["hue"]])

    def step(self, dt: float):
        self.t += dt
        for ring in self.rings:
            ring["rot"] = (ring["rot"] + ring["w"] * dt) % math.tau
        for p in self.particles:
            p[1][1] += self.GRAVITY * 0.6 * dt
            p[0] = p[0] + p[1] * dt
            p[2] -= dt * 0.9
        self.particles = [p for p in self.particles if p[2] > 0]

        self.vel[1] += self.GRAVITY * dt
        self.pos += self.vel * dt
        offset = self.pos - self.CENTER
        dist = float(np.hypot(*offset))
        if self.rings:
            ring = self.rings[0]
            inner = ring["r"] - self.LINE_W / 2 - self.BALL_R
            angle = math.atan2(offset[1], offset[0]) % math.tau
            in_gap = (angle - ring["rot"]) % math.tau < self.GAP
            if dist > ring["r"] + self.LINE_W / 2 + self.BALL_R:
                if in_gap:
                    self.burst(self.rings.pop(0))
                    self.events.append((self.t, "break"))
                else:  # clipped the ring's edge on the way out of the gap: pull back inside
                    self.pos = self.CENTER + offset / dist * (inner - 0.5)
                    self.vel *= -0.8
            elif dist >= inner and not in_gap:
                n = offset / dist
                self.vel -= 2 * np.dot(self.vel, n) * n
                self.vel += np.array([-n[1], n[0]]) * self.rng.uniform(-120, 120)
                speed = float(np.hypot(*self.vel))
                self.vel *= np.clip(speed * 1.03, 700, 1350) / speed
                self.pos = self.CENTER + n * (inner - 0.5)
                self.events.append((self.t, "bounce"))
        elif self.escaped_at is None:
            self.escaped_at = self.t
        if self.escaped_at is not None and self.t - self.escaped_at > 1.2:
            self.new_round()

    def advance_frame(self):
        for _ in range(8):
            self.step(1 / FPS / 8)
        self.trail = (self.trail + [self.pos.copy()])[-12:]

    def draw(self, ctx: cairo.Context):
        cx, cy = self.CENTER
        bg = cairo.RadialGradient(cx, cy, 50, cx, cy, 1300)
        bg.add_color_stop_rgb(0, 0.09, 0.06, 0.16)
        bg.add_color_stop_rgb(1, 0.02, 0.02, 0.05)
        ctx.set_source(bg)
        ctx.paint()
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        for ring in self.rings:
            r, g, b = colorsys.hsv_to_rgb(ring["hue"], 0.75, 1)
            start, end = ring["rot"] + self.GAP, ring["rot"] + math.tau
            for width, alpha in ((self.LINE_W + 22, 0.12), (self.LINE_W + 10, 0.22), (self.LINE_W, 1)):
                ctx.set_source_rgba(r, g, b, alpha)
                ctx.set_line_width(width)
                ctx.new_path()
                ctx.arc(cx, cy, ring["r"], start, end)
                ctx.stroke()
        for p, _, life, hue in self.particles:
            r, g, b = colorsys.hsv_to_rgb(hue, 0.7, 1)
            ctx.set_source_rgba(r, g, b, max(life, 0))
            ctx.new_path()
            ctx.arc(p[0], p[1], 3 + 4 * life, 0, math.tau)
            ctx.fill()
        r, g, b = colorsys.hsv_to_rgb(self.hue, 0.6, 1)
        for i, p in enumerate(self.trail[:-1]):
            k = (i + 1) / len(self.trail)
            ctx.set_source_rgba(r, g, b, 0.35 * k)
            ctx.new_path()
            ctx.arc(p[0], p[1], self.BALL_R * (0.4 + 0.6 * k), 0, math.tau)
            ctx.fill()
        ctx.set_source_rgba(r, g, b, 0.35)
        ctx.new_path()
        ctx.arc(self.pos[0], self.pos[1], self.BALL_R + 10, 0, math.tau)
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.new_path()
        ctx.arc(self.pos[0], self.pos[1], self.BALL_R, 0, math.tau)
        ctx.fill()


# ---------------------------------------------------------------- overlays

def title_card(title: str, path: Path) -> int:
    """White rounded card with the channel avatar, name and the hook. Returns its height."""
    font_name = ImageFont.truetype(FONT_BOLD, 40)
    font_handle = ImageFont.truetype(FONT_BOLD, 30)
    font_title = ImageFont.truetype(FONT_BOLD, 54)
    card_w, pad = 920, 44
    lines, line = [], ""
    for word in title.split():
        trial = f"{line} {word}".strip()
        if font_title.getlength(trial) > card_w - 2 * pad:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    card_h = pad + 140 + len(lines) * 68 + pad - 10
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=36, fill=(255, 255, 255, 255))
    avatar = Image.open(HERE / "profile-pic.jpg").convert("RGB").resize((110, 110), Image.LANCZOS)
    mask = Image.new("L", (110, 110), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 109, 109), fill=255)
    card.paste(avatar, (pad, pad), mask)
    d.text((pad + 130, pad + 14), CHANNEL, font=font_name, fill=(20, 20, 30))
    d.text((pad + 130, pad + 62), HANDLE, font=font_handle, fill=(130, 130, 150))
    y = pad + 140
    for l in lines:
        d.text((pad, y), l, font=font_title, fill=(15, 15, 20))
        y += 68
    card.save(path)
    return card_h


def ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def captions(words: list, path: Path, total: float):
    """Chunks of up to 3 words; the word being spoken turns yellow and pops."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Inter Black,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,9,5,5,40,40,0,1
Style: Handle,Inter ExtraBold,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,8,40,40,110,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{ass_time(0)},{ass_time(total)},Handle,,0,0,0,,{HANDLE}
"""
    chunks, current = [], []
    for w in (w for w in words if w[3] > 0):
        if current and len(" ".join(x[0] for x in current + [w])) > MAX_CAPTION_CHARS:
            chunks.append(current)
            current = []
        current.append(w)
        if len(current) == WORDS_PER_CAPTION or re.search(r"[.!?,]$", w[0]):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)

    events = []
    for c, chunk in enumerate(chunks):
        chunk_end = chunks[c + 1][0][1] if c + 1 < len(chunks) else chunk[-1][2] + 0.4
        texts = [w[0].upper().replace("{", "(").replace("}", ")") for w in chunk]
        for i, (_, start, _, _) in enumerate(chunk):
            end = chunk[i + 1][1] if i + 1 < len(chunk) else chunk_end
            line = " ".join(
                "{\\c&H0033E6FF&\\fscx112\\fscy112}" + t + "{\\c&H00FFFFFF&\\fscx100\\fscy100}" if j == i else t
                for j, t in enumerate(texts)
            )
            pop = "\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{{\\pos(540,{CAPTION_Y}){pop}}}{line}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- assembly

def make_video(story_path: Path, voice: str, models, seed: int) -> Path:
    tts, asr = models
    lines = [l.strip() for l in story_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    title, body = lines[0], " ".join(lines[1:])
    sentences = [title] + [s for s in re.split(r"(?<=[.!?])\s+", body) if s]

    voice_audio, words = narrate(tts, asr, sentences, voice)
    total = len(voice_audio) / SR + 0.8
    title_end = max(w[2] for w in words if w[3] == 0) + 0.25
    frames = int(total * FPS)

    sim = RingEscape(seed)  # physics-only pass, to time the sound effects
    for _ in range(frames):
        sim.advance_frame()
    n = int(total * SR)
    fit = lambda x: np.pad(x, (0, max(0, n - len(x))))[:n]
    mix = fit(voice_audio) + 0.5 * fit(lofi_bed(total, np.random.default_rng(seed))) + fit(sfx_track(sim.events, total))

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sf.write(work / "mix.wav", mix / max(1.0, float(np.abs(mix).max())), SR)
        card_h = title_card(title, work / "card.png")
        captions(words, work / "subs.ass", total)
        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / f"{story_path.stem}.mp4"
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                "-i", str(work / "mix.wav"),
                "-loop", "1", "-r", str(FPS), "-i", str(work / "card.png"),
                "-filter_complex",
                f"[2:v]format=rgba,fade=in:st=0:d=0.2:alpha=1,fade=out:st={title_end - 0.25:.2f}:d=0.25:alpha=1[card];"
                f"[0:v][card]overlay=(W-w)/2:{RING_CENTER_Y - card_h // 2}:enable='lte(t,{title_end:.2f})':shortest=1,"
                f"ass={work / 'subs.ass'},format=yuv420p[v];"
                f"[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]",
                "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(out),
            ],
            stdin=subprocess.PIPE,
        )
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        ctx = cairo.Context(surface)
        sim = RingEscape(seed)  # same seed, so the picture matches the sounds
        for _ in range(frames):
            sim.advance_frame()
            sim.draw(ctx)
            surface.flush()
            ffmpeg.stdin.write(surface.get_data())
        ffmpeg.stdin.close()
        if ffmpeg.wait():
            raise RuntimeError(f"ffmpeg failed for {story_path}")
    print(f"{out}  ({total:.0f}s, {len(sim.events)} bounces/breaks)")
    return out


VOICES = ["am_michael", "am_fenrir", "af_heart"]

if __name__ == "__main__":
    models = load_models()
    for i, path in enumerate(sys.argv[1:]):
        make_video(Path(path), VOICES[i % len(VOICES)], models, seed=i + 7)
