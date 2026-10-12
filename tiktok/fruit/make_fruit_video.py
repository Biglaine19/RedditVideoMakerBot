"""Builds an AI fruit-people drama episode: scene images + a voice per character + captions + music.

    python tiktok/fruit/make_fruit_video.py tiktok/fruit/ep1

The episode folder holds episode.txt (script) and scene1.png, scene2.png, ... (from make_scenes.py).
episode.txt:
    # Title
    hook: TEXT SHOWN OVER THE FIRST SECONDS
    voice <speaker> <kokoro voice>
    [1 sting shake]          <- scene number, optional effects
    speaker: line
"""
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cairo
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from make_story_video import FPS, H, SR, W, ass_time, captions, load_models, note, resample, soften, word_times  # noqa: E402

LINE_PAUSE = 0.28
FLASH = 0.12


def parse(path: Path):
    hook, voices, scenes = "", {}, []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("hook:"):
            hook = line[5:].strip()
        elif line.startswith("voice "):
            _, who, voice = line.split()
            voices[who] = voice
        elif m := re.fullmatch(r"\[(\d+)([a-z ]*)\]", line):
            scenes.append({"n": m[1], "fx": m[2].split(), "lines": []})
        else:
            who, text = line.split(":", 1)
            scenes[-1]["lines"].append((who.strip(), text.strip()))
    return hook, voices, scenes


def dark_bed(length: float) -> np.ndarray:
    """Slow, tense minor-chord pad with a heartbeat pulse."""
    midi = lambda m: 440 * 2 ** ((m - 69) / 12)
    chords = [[45, 57, 60, 64], [41, 57, 60, 65], [38, 57, 62, 65], [40, 56, 59, 64]]  # Am F Dm E
    bar = 4.0
    out = np.zeros(int((length + bar * 2) * SR), np.float32)
    t, n = 0.0, 0
    while t < length:
        for m in chords[n % 4]:
            tt = np.arange(int(bar * 1.3 * SR)) / SR
            env = np.minimum(tt / 0.8, 1) * np.exp(-np.maximum(tt - bar, 0) / 0.3)
            tone = sum(a * np.sin(2 * np.pi * midi(m) * k * tt + k) for k, a in ((1, 1), (2, 0.4), (3, 0.2), (4, 0.1)))
            i = int(t * SR)
            out[i : i + len(tt)] += (0.05 * tone * env).astype(np.float32)
        for beat in (0, 0.35, 2, 2.35):
            kt = np.arange(int(0.3 * SR)) / SR
            i = int((t + beat) * SR)
            out[i : i + len(kt)] += (0.25 * np.sin(2 * np.pi * (45 + 30 * np.exp(-kt * 25)) * kt) * np.exp(-kt * 12)).astype(np.float32)
        t += bar
        n += 1
    return soften(out, 2000)[: int(length * SR)]


def sting() -> np.ndarray:
    """The 'dun dun DUN' hit for reveals."""
    out = np.zeros(int(2.6 * SR), np.float32)
    for k, (at, m) in enumerate(((0, 38), (0.28, 38), (0.56, 37))):
        f = 440 * 2 ** ((m - 69) / 12)
        hit = sum(note(f * mult, 2.0 if k == 2 else 0.5, 0.9 if k == 2 else 0.25, (1, 0.6, 0.4, 0.25)) for mult in (1, 2, 1.5))
        i = int(at * SR)
        out[i : i + len(hit)] += 0.22 * hit[: len(out) - i]
    return out


def whoosh(rng) -> np.ndarray:
    n = int(0.35 * SR)
    noise = soften(rng.standard_normal(n).astype(np.float32), 1500)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return (0.5 * noise * env).astype(np.float32)


def build_audio(tts, asr, voices, scenes):
    voice_parts, words, t = [], [], 0.6
    voice_parts.append(np.zeros(int(0.6 * SR), np.float32))
    for scene in scenes:
        scene["start"] = t
        for who, text in scene["lines"]:
            audio, sr = tts.create(text, voice=voices[who], speed=1.08, lang="en-us")
            words += [(w, t + s, t + e, 1) for w, s, e in word_times(asr, audio, sr, text)]
            clip = resample(audio, sr, SR)
            voice_parts += [clip, np.zeros(int(LINE_PAUSE * SR), np.float32)]
            t += len(clip) / SR + LINE_PAUSE
        scene["end"] = t
    scenes[-1]["end"] = t + 1.6  # room for the "Part 2?" card
    total = scenes[-1]["end"]
    n = int(total * SR)
    fit = lambda x: np.pad(x, (0, max(0, n - len(x))))[:n]
    mix = fit(np.concatenate(voice_parts)) + 0.6 * fit(dark_bed(total))
    rng = np.random.default_rng(3)
    for scene in scenes[1:]:
        i = max(0, int((scene["start"] - 0.2) * SR))
        w = whoosh(rng)
        mix[i : i + len(w)] += w[: n - i]
    for scene in scenes:
        if "sting" in scene["fx"]:
            s, i = sting(), int(scene["start"] * SR)
            mix[i : i + len(s)] += s[: n - i]
    return mix, words, total


def render(ep: Path, scenes, total: float, subs: Path, audio: Path, out: Path):
    images = {s["n"]: cairo.ImageSurface.create_from_png(str(ep / f"scene{s['n']}.png")) for s in scenes}
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(audio), "-filter_complex", f"[0:v]unsharp=7:7:0.9,ass={subs},format=yuv420p[v];[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE,
    )
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surface)
    rng = np.random.default_rng(1)
    for f in range(int(total * FPS)):
        t = f / FPS
        idx = next(i for i, s in enumerate(scenes) if t < s["end"] or i == len(scenes) - 1)
        scene, img = scenes[idx], images[scenes[idx]["n"]]
        local = t - (scene["start"] if idx else 0)
        span = scene["end"] - (scene["start"] if idx else 0)
        progress = min(max(local / span, 0), 1)
        cover = max(W / img.get_width(), H / img.get_height())
        zoom = 1.02 + 0.10 * (progress if idx % 2 == 0 else 1 - progress)
        if "sting" in scene["fx"] and local < 0.5:
            zoom += 0.12 * (1 - local / 0.5) ** 2  # punch-in on reveals
        dx = dy = 0.0
        if "shake" in scene["fx"] and local < 1.4:
            amp = 18 * (1 - local / 1.4)
            dx, dy = rng.uniform(-amp, amp), rng.uniform(-amp, amp)
        ctx.save()
        ctx.set_source_rgb(0, 0, 0)
        ctx.paint()
        ctx.translate(W / 2 + dx, H / 2 + dy)
        ctx.scale(cover * zoom, cover * zoom)
        ctx.translate(-img.get_width() / 2, -img.get_height() / 2)
        ctx.set_source_surface(img, 0, 0)
        ctx.get_source().set_filter(cairo.FILTER_GOOD)
        ctx.paint()
        ctx.restore()
        shade = cairo.LinearGradient(0, H * 0.55, 0, H)  # darken the bottom so captions read
        shade.add_color_stop_rgba(0, 0, 0, 0, 0)
        shade.add_color_stop_rgba(1, 0, 0, 0, 0.55)
        ctx.set_source(shade)
        ctx.paint()
        if idx and local < FLASH:
            ctx.set_source_rgba(1, 1, 1, 0.85 * (1 - local / FLASH))
            ctx.paint()
        surface.flush()
        ffmpeg.stdin.write(surface.get_data())
    ffmpeg.stdin.close()
    if ffmpeg.wait():
        raise RuntimeError("ffmpeg failed")


def main(ep: Path):
    hook, voices, scenes = parse(ep / "episode.txt")
    tts, asr = load_models()
    mix, words, total = build_audio(tts, asr, voices, scenes)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sf.write(work / "mix.wav", mix / max(1.0, float(np.abs(mix).max())), SR)
        subs = work / "subs.ass"
        captions(words, subs, total)
        extra = [
            "Style: Hook,Inter Black,74,&H0033E6FF,&H0033E6FF,&H00000000,&HC8000000,0,0,0,0,100,100,0,0,3,16,0,8,60,60,250,1",
            "Style: End,Inter Black,150,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,12,6,5,40,40,0,1",
        ]
        text = subs.read_text(encoding="utf-8").replace("\n\n[Events]", "\n" + "\n".join(extra) + "\n\n[Events]")
        text += f"Dialogue: 2,{ass_time(0)},{ass_time(3.2)},Hook,,0,0,0,,{{\\fad(150,200)}}{hook}\n"
        text += f"Dialogue: 2,{ass_time(total - 1.5)},{ass_time(total)},End,,0,0,0,,{{\\pos(540,820)\\fscx60\\fscy60\\t(0,200,\\fscx100\\fscy100)}}PART 2?\n"
        subs.write_text(text, encoding="utf-8")
        out = Path(__file__).parent.parent / "videos" / f"fruit-{ep.name}.mp4"
        out.parent.mkdir(exist_ok=True)
        render(ep, scenes, total, subs, work / "mix.wav", out)
    print(f"{out}  ({total:.0f}s)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
