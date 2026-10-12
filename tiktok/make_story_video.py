"""Turns a story text file into a vertical TikTok video with voiceover and big word-chunk captions.

Story file format: first line is the hook/title, every following line is the body.
Needs ffmpeg on PATH and the gTTS package (pip install gTTS).

    python tiktok/make_story_video.py tiktok/stories/01-approval-emails.txt [more files...]

Videos land in tiktok/videos/.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from gtts import gTTS

HANDLE = "@nightowlstories"
SPEED = 1.25  # gTTS reads slowly; TikTok narration sits around 1.2-1.3x
WORDS_PER_CAPTION = 3
OUT_DIR = Path(__file__).parent / "videos"

# Animated backgrounds rendered by ffmpeg, so no gameplay footage has to be downloaded.
BACKGROUNDS = [
    "gradients=s=540x960:r=30:n=5:speed=0.02:c0=0x2b0a3d:c1=0x00b8e6:c2=0xff2d95:c3=0x5a2bff:c4=0x0a0a2a",
    "gradients=s=540x960:r=30:n=4:speed=0.025:type=radial:c0=0x1a0630:c1=0xff7a00:c2=0xff2d95:c3=0x0a0a2a",
    "life=s=180x320:r=12:mold=12:ratio=0.2:life_color=#00e5ff:death_color=#1a0630:mold_color=#2a0b45,fps=30",
]

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Inter Black,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,6,3,5,50,50,0,1
Style: Title,Inter ExtraBold,46,&H00000000,&H00000000,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,3,18,0,5,70,70,0,1
Style: Handle,Inter ExtraBold,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,8,40,40,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
CAPTION_COLOURS = ["&H00FFFFFF", "&H0000E5FF"]  # white, yellow


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def duration(path: Path) -> float:
    info = run("ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path))
    return float(json.loads(info)["format"]["duration"])


def ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def split_sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def narrate(sentence: str, work: Path, index: int) -> Path:
    raw, wav = work / f"{index}.mp3", work / f"{index}.wav"
    gTTS(sentence, lang="en", tld="com").save(str(raw))
    run("ffmpeg", "-y", "-i", str(raw), "-af", f"atempo={SPEED}", "-ar", "44100", "-ac", "1", str(wav))
    return wav


def caption_events(sentence: str, start: float, length: float, colour_offset: int) -> list:
    words = sentence.split()
    chunks = [" ".join(words[i : i + WORDS_PER_CAPTION]) for i in range(0, len(words), WORDS_PER_CAPTION)]
    weights = [len(c) + 3 for c in chunks]
    events, t = [], start
    for i, (chunk, weight) in enumerate(zip(chunks, weights)):
        end = t + length * weight / sum(weights)
        colour = CAPTION_COLOURS[(colour_offset + i) % len(CAPTION_COLOURS)]
        events.append(
            f"Dialogue: 0,{ass_time(t)},{ass_time(end)},Caption,,0,0,0,,"
            f"{{\\c{colour}\\fscx90\\fscy90\\t(0,80,\\fscx100\\fscy100)}}{ass_text(chunk.upper())}"
        )
        t = end
    return events


def make_video(story_path: Path, background: str) -> Path:
    lines = [l.strip() for l in story_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    title, body = lines[0], " ".join(lines[1:])
    sentences = [title] + split_sentences(body)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        wavs = [narrate(s, work, i) for i, s in enumerate(sentences)]
        lengths = [duration(w) for w in wavs]

        (work / "list.txt").write_text("".join(f"file '{w}'\n" for w in wavs))
        audio = work / "voice.wav"
        run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(audio))
        total = sum(lengths) + 0.6

        events = [
            f"Dialogue: 1,{ass_time(0)},{ass_time(lengths[0])},Title,,0,0,0,,{ass_text(title)}",
            f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Handle,,0,0,0,,{HANDLE}",
        ]
        t, colour_offset = lengths[0], 0
        for sentence, length in zip(sentences[1:], lengths[1:]):
            events += caption_events(sentence, t, length, colour_offset)
            colour_offset += 1
            t += length
        subs = work / "subs.ass"
        subs.write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")

        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / f"{story_path.stem}.mp4"
        run(
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", background,
            "-i", str(audio),
            "-filter_complex",
            f"[0:v]scale=1080:1920:flags=bicubic,eq=brightness=-0.12:saturation=1.2,"
            f"ass={subs},format=yuv420p[v]",
            "-map", "[v]", "-map", "1:a",
            "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-r", "30",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(out),
        )
    print(f"{out}  ({total:.0f}s)")
    return out


if __name__ == "__main__":
    for i, path in enumerate(sys.argv[1:]):
        make_video(Path(path), BACKGROUNDS[i % len(BACKGROUNDS)])
