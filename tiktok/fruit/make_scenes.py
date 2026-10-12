"""Renders the scene images for a fruit-people episode with SDXL + SDXL-Lightning (both allow commercial use).

If ep/refN.jpg exists (e.g. a small Canva draft), the scene is redrawn from it with img2img so the
composition and characters carry over; otherwise it is generated from the prompt alone.

    python tiktok/fruit/make_scenes.py tiktok/fruit/ep1 [scene numbers...]

Set HF_HOME / cache_dir below to wherever the models were downloaded.
"""
import json
import sys
from pathlib import Path

import torch
from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLImg2ImgPipeline, StableDiffusionXLPipeline, UNet2DConditionModel
from huggingface_hub import hf_hub_download
from PIL import Image
from safetensors.torch import load_file

CACHE = "/home/user/models/hf"
BASE = "stabilityai/stable-diffusion-xl-base-1.0"
SIZE = (768, 1344)
STYLE = ("Pixar style 3D animation still, anthropomorphic fruit characters with expressive cartoon faces and human arms, "
         "cinematic soap opera lighting, vibrant colors, highly detailed, sharp focus")
NEGATIVE = "text, watermark, letters, blurry, deformed, extra limbs, realistic human skin, photo of a person"


def load():
    torch.set_num_threads(torch.get_num_threads())
    unet = UNet2DConditionModel.from_config(BASE, subfolder="unet", cache_dir=CACHE)
    unet.load_state_dict(load_file(hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_unet.safetensors", cache_dir=CACHE)))
    vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", cache_dir=CACHE)
    pipe = StableDiffusionXLPipeline.from_pretrained(BASE, unet=unet, vae=vae, variant="fp16", cache_dir=CACHE,
                                                     torch_dtype=torch.bfloat16)
    pipe = pipe.to(torch.bfloat16)
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    return pipe, StableDiffusionXLImg2ImgPipeline(**pipe.components)


def main(ep: Path, only: list):
    scenes = json.loads((ep / "scenes.json").read_text())
    txt2img, img2img = load()
    for number, prompt in scenes.items():
        if only and number not in only:
            continue
        full = f"{STYLE}, {prompt}"
        ref = ep / f"ref{number}.jpg"
        generator = torch.Generator().manual_seed(int(number) * 101)
        if ref.exists():
            init = Image.open(ref).convert("RGB").resize(SIZE, Image.LANCZOS)
            image = img2img(full, negative_prompt=NEGATIVE, image=init, strength=0.62, num_inference_steps=8,
                            guidance_scale=0, generator=generator).images[0]
        else:
            image = txt2img(full, negative_prompt=NEGATIVE, width=SIZE[0], height=SIZE[1], num_inference_steps=4,
                            guidance_scale=0, generator=generator).images[0]
        image.save(ep / f"scene{number}.png")
        print("saved", ep / f"scene{number}.png", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2:])
