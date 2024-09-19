"""
THis is the main file for the gradio web demo. It uses the CogVideoX-2B model to generate videos gradio web demo.
set environment variable OPENAI_API_KEY to use the OpenAI API to enhance the prompt.

Usage:
    OpenAI_API_KEY=your_openai_api_key OpenAI_BASE_URL=https://api.openai.com/v1 python inference/gradio_web_demo.py
"""

import math
import os
import random
import threading
import time
import cv2
import tempfile
import imageio_ffmpeg
import gradio as gr
import torch
from PIL import Image
from diffusers import (
    CogVideoXPipeline,
    CogVideoXDPMScheduler,
    CogVideoXVideoToVideoPipeline,
    CogVideoXImageToVideoPipeline,
    CogVideoXTransformer3DModel,
)
from diffusers.utils import export_to_video, load_video, load_image
from datetime import datetime, timedelta

from diffusers.image_processor import VaeImageProcessor
from openai import OpenAI
import moviepy.editor as mp
import utils
from rife_model import load_rife_model, rife_inference_with_latents
from huggingface_hub import hf_hub_download, snapshot_download


pipe = None
pipe_image = None
pipe_video = None
device = "cuda" if torch.cuda.is_available() else "cpu"
hf_hub_download(repo_id="ai-forever/Real-ESRGAN", filename="RealESRGAN_x4.pth", local_dir="model_real_esran")
snapshot_download(repo_id="AlexWortega/RIFE", local_dir="model_rife")


initialized = None


# 0. Unified pipe init
def init(name, image_input, video_input, dtype_str):
    if image_input != None:
        # img2vid
        init_img2vid(name, dtype_str)
    elif video_input != None:
        # vid2vid
        init_vid2vid(name, dtype_str)
    else:
        # txt2vid
        init_txt2vid(name, dtype_str)

# 1. initialize core pipe
def init_txt2vid(name, dtype_str):
    global pipe
    torch.cuda.empty_cache()
    if pipe == None:
        if dtype_str == "bfloat16":
            dtype = torch.bfloat16
        elif dtype_str == "float16":
            dtype = torch.float16
        pipe = CogVideoXPipeline.from_pretrained(name, torch_dtype=dtype).to(device)
        pipe.scheduler = CogVideoXDPMScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    return dtype
        
# 2. initialize vid2vid pipe
def init_vid2vid(name, dtype_str):
    global pipe
    global pipe_video
    torch.cuda.empty_cache()
    if pipe_video == None:
        if dtype_str == "bfloat16":
            dtype = torch.bfloat16
        elif dtype_str == "float16":
            dtype = torch.float16
        # init pipe
        if pipe == None:
            pipe = CogVideoXPipeline.from_pretrained(name, torch_dtype=dtype).to(device)
            pipe.scheduler = CogVideoXDPMScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
        # init pipe_video
        pipe_video = CogVideoXVideoToVideoPipeline.from_pretrained(
            name,
            transformer=pipe.transformer,
            vae=pipe.vae,
            scheduler=pipe.scheduler,
            tokenizer=pipe.tokenizer,
            text_encoder=pipe.text_encoder,
            torch_dtype=dtype
        ).to(device)

# 3. initialize img2vid pipe
def init_img2vid(name, dtype_str):
    global pipe
    global pipe_image
    torch.cuda.empty_cache()
    if pipe_image == None:
        if dtype_str == "bfloat16":
            dtype = torch.bfloat16
        elif dtype_str == "float16":
            dtype = torch.float16
        transformer = CogVideoXTransformer3DModel.from_pretrained("THUDM/CogVideoX-5b-I2V", torch_dtype=dtype)
        pipe_image = CogVideoXImageToVideoPipeline.from_pretrained(
            "THUDM/CogVideoX-5b-I2V",
            transformer=transformer,
            vae=pipe.vae,
            scheduler=pipe.scheduler,
            tokenizer=pipe.tokenizer,
            text_encoder=pipe.text_encoder,
            torch_dtype=dtype
        ).to(device)

os.makedirs("./output", exist_ok=True)
os.makedirs("./gradio_tmp", exist_ok=True)

sys_prompt = """You are part of a team of bots that creates videos. You work with an assistant bot that will draw anything you say in square brackets.

For example , outputting " a beautiful morning in the woods with the sun peaking through the trees " will trigger your partner bot to output an video of a forest morning , as described. You will be prompted by people looking to create detailed , amazing videos. The way to accomplish this is to take their short prompts and make them extremely detailed and descriptive.
There are a few rules to follow:

You will only ever output a single video description per user request.

When modifications are requested , you should not simply make the description longer . You should refactor the entire description to integrate the suggestions.
Other times the user will not want modifications , but instead want a new image . In this case , you should ignore your previous conversation with the user.

Video descriptions must have the same num of words as examples below. Extra words will be ignored.
"""


def convert_prompt(prompt: str, retry_times: int = 3) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        return prompt
    client = OpenAI()
    text = prompt.strip()

    for i in range(retry_times):
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": 'Create an imaginative video descriptive caption or modify an earlier caption for the user input : "a girl is on the beach"',
                },
                {
                    "role": "assistant",
                    "content": "A radiant woman stands on a deserted beach, arms outstretched, wearing a beige trench coat, white blouse, light blue jeans, and chic boots, against a backdrop of soft sky and sea. Moments later, she is seen mid-twirl, arms exuberant, with the lighting suggesting dawn or dusk. Then, she runs along the beach, her attire complemented by an off-white scarf and black ankle boots, the tranquil sea behind her. Finally, she holds a paper airplane, her pose reflecting joy and freedom, with the ocean's gentle waves and the sky's soft pastel hues enhancing the serene ambiance.",
                },
                {
                    "role": "user",
                    "content": 'Create an imaginative video descriptive caption or modify an earlier caption for the user input : "A man jogging on a football field"',
                },
                {
                    "role": "assistant",
                    "content": "A determined man in athletic attire, including a blue long-sleeve shirt, black shorts, and blue socks, jogs around a snow-covered soccer field, showcasing his solitary exercise in a quiet, overcast setting. His long dreadlocks, focused expression, and the serene winter backdrop highlight his dedication to fitness. As he moves, his attire, consisting of a blue sports sweatshirt, black athletic pants, gloves, and sneakers, grips the snowy ground. He is seen running past a chain-link fence enclosing the playground area, with a basketball hoop and children's slide, suggesting a moment of solitary exercise amidst the empty field.",
                },
                {
                    "role": "user",
                    "content": 'Create an imaginative video descriptive caption or modify an earlier caption for the user input : " A woman is dancing, HD footage, close-up"',
                },
                {
                    "role": "assistant",
                    "content": "A young woman with her hair in an updo and wearing a teal hoodie stands against a light backdrop, initially looking over her shoulder with a contemplative expression. She then confidently makes a subtle dance move, suggesting rhythm and movement. Next, she appears poised and focused, looking directly at the camera. Her expression shifts to one of introspection as she gazes downward slightly. Finally, she dances with confidence, her left hand over her heart, symbolizing a poignant moment, all while dressed in the same teal hoodie against a plain, light-colored background.",
                },
                {
                    "role": "user",
                    "content": f'Create an imaginative video descriptive caption or modify an earlier caption in ENGLISH for the user input: "{text}"',
                },
            ],
            model="gpt-4o",
            temperature=0.01,
            top_p=0.7,
            stream=False,
            max_tokens=250,
        )
        if response.choices:
            return response.choices[0].message.content
    return prompt

def infer(
    name: str,
    prompt: str,
    image_input: str,
    video_input: str,
    strength: float,
    num_inference_steps: int,
    guidance_scale: float,
    seed: int = -1,
    progress=gr.Progress(track_tqdm=True),
):
    global pipe
    global pipe_video
    global pipe_image

    init(name, image_input, video_input, dtype)

    if seed == -1:
        seed = random.randint(0, 2**8 - 1)

    if video_input is not None:
        resized_video_input = resize_video(video_input)
        video = load_video(resized_video_input)[:49]  # Limit to 49 frames
        video_pt = pipe_video(
            video=video,
            prompt=prompt,
            num_inference_steps=num_inference_steps,
            num_videos_per_prompt=1,
            strength=strength,
            use_dynamic_cfg=True,
            output_type="pt",
            guidance_scale=guidance_scale,
            generator=torch.Generator(device="cpu").manual_seed(seed),
        ).frames
    elif image_input is not None:
        image_input = Image.fromarray(image_input).resize(size=(720, 480))  # Convert to PIL
        image = load_image(image_input)
        video_pt = pipe_image(
            image=image,
            prompt=prompt,
            num_inference_steps=num_inference_steps,
            num_videos_per_prompt=1,
            use_dynamic_cfg=True,
            output_type="pt",
            guidance_scale=guidance_scale,
            generator=torch.Generator(device="cpu").manual_seed(seed),
        ).frames
    else:
        video_pt = pipe(
            prompt=prompt,
            num_videos_per_prompt=1,
            num_inference_steps=num_inference_steps,
            num_frames=49,
            use_dynamic_cfg=True,
            output_type="pt",
            guidance_scale=guidance_scale,
            generator=torch.Generator(device="cpu").manual_seed(seed),
        ).frames

    return (video_pt, seed)


def resize_video(input_path, target_size=(720, 480)):
    print(f"resize video {input_path}")

    # Load the video clip
    clip = mp.VideoFileClip(input_path)

    # Remove audio
    clip = clip.without_audio()

    # Calculate the scaling factor
    width_ratio = target_size[0] / clip.w
    height_ratio = target_size[1] / clip.h
    scale_factor = min(width_ratio, height_ratio)

    print(f"resize {scale_factor}")

    # Resize the clip
    resized_clip = clip.resize(scale_factor)

    # If the resized clip is smaller than the target size, pad it
    if resized_clip.w < target_size[0] or resized_clip.h < target_size[1]:
        resized_clip = resized_clip.on_color(
            size=target_size,
            color=(0, 0, 0),  # Black padding
            pos='center'
        )

    # Save to a temporary file
    input_dir = os.path.dirname(input_path)
    temp_output = os.path.join(input_dir, "temp_video.mp4")

    resized_clip.write_videofile(temp_output, fps=8)

    # Close the clips
    clip.close()
    resized_clip.close()

    return temp_output


def save_video(tensor):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = f"./output/{timestamp}.mp4"
    os.makedirs(os.path.dirname(video_path), exist_ok=True)
    export_to_video(tensor, video_path)
    return video_path


def convert_to_gif(video_path):
    clip = mp.VideoFileClip(video_path)
    clip = clip.set_fps(8)
    clip = clip.resize(height=240)
    gif_path = video_path.replace(".mp4", ".gif")
    clip.write_gif(gif_path, fps=8)
    return gif_path


def delete_old_files():
    while True:
        now = datetime.now()
        cutoff = now - timedelta(minutes=10)
        directories = ["./output", "./gradio_tmp"]

        for directory in directories:
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.isfile(file_path):
                    file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if file_mtime < cutoff:
                        os.remove(file_path)
        time.sleep(600)


threading.Thread(target=delete_old_files, daemon=True).start()

with gr.Blocks() as demo:
#    gr.Markdown("""
#           <div style="text-align: center; font-size: 32px; font-weight: bold; margin-bottom: 20px;">
#               CogVideoX-2B Huggingface Space🤗
#           </div>
#           <div style="text-align: center;">
#               <a href="https://huggingface.co/THUDM/CogVideoX-2B">🤗 2B Model Hub</a> |
#               <a href="https://github.com/THUDM/CogVideo">🌐 Github</a> |
#               <a href="https://arxiv.org/pdf/2408.06072">📜 arxiv </a>
#           </div>
#
#           <div style="text-align: center; font-size: 15px; font-weight: bold; color: red; margin-bottom: 20px;">
#            ⚠️ This demo is for academic research and experiential use only.
#            Users should strictly adhere to local laws and ethics.
#            </div>
#           """)
    with gr.Tabs(selected=0) as tabs:
        with gr.TabItem("text-to-video", id=0):
            with gr.Row():
                with gr.Column():
                    prompt = gr.Textbox(label="Prompt (Less than 200 Words. The more detailed the better.)", placeholder="Enter your prompt here", lines=5)

                    strength = gr.Number(value=0.8, minimum=0.1, maximum=1.0, step=0.01, label="Strength", visible=False)
                    with gr.Row():
                        gr.Markdown(
                            "✨ To enhance the prompt, either set the OPENAI_API_KEY variable from the Configure menu (if you have an OpenAI API key), or just use chatgpt to enhance the prompt manually (Recommended)",
                        )
                        enhance_button = gr.Button("✨ Enhance Prompt(Optional)")

                    with gr.Row():
                        model_choice = gr.Dropdown(["THUDM/CogVideoX-2b", "THUDM/CogVideoX-5b"], value="THUDM/CogVideoX-2b", label="Model")
                    with gr.Row():
                        num_inference_steps = gr.Number(label="Inference Steps", value=50)
                        guidance_scale = gr.Number(label="Guidance Scale", value=6.0)
                    with gr.Row():
                        dtype_choice = gr.Radio(["bfloat16", "float16"], label="dtype (older machines may not support bfloat16. try float16 if bfloat16 doesn't work)", value="bfloat16")
                    with gr.Row():
                        seed_param = gr.Number(
                            label="Inference Seed (Enter a positive number, -1 for random)", value=-1
                        )
                    with gr.Row():
                        enable_scale = gr.Checkbox(label="Super-Resolution (720 × 480 -> 2880 × 1920)", value=False)
                        enable_rife = gr.Checkbox(label="Frame Interpolation (8fps -> 16fps)", value=False)
                    generate_button = gr.Button("🎬 Generate Video")

                with gr.Column():
                    video_output = gr.Video(label="CogVideoX Generate Video", width=720, height=480)
                    with gr.Row():
                        download_video_button = gr.File(label="📥 Download Video", visible=False)
                        download_gif_button = gr.File(label="📥 Download GIF", visible=False)
                        send_to_vid2vid_button = gr.Button("Send to video-to-video", visible=False)
            gr.Markdown("""
            <table border="0" style="width: 100%; text-align: left; margin-top: 20px;">
                <div style="text-align: center; font-size: 24px; font-weight: bold; margin-bottom: 20px;">
                    Demo Videos with 50 Inference Steps and 6.0 Guidance Scale.
                </div>
                <tr>
                    <td style="width: 25%; vertical-align: top; font-size: 0.8em;">
                        <p>A detailed wooden toy ship with intricately carved masts and sails is seen gliding smoothly over a plush, blue carpet that mimics the waves of the sea. The ship's hull is painted a rich brown, with tiny windows. The carpet, soft and textured, provides a perfect backdrop, resembling an oceanic expanse. Surrounding the ship are various other toys and children's items, hinting at a playful environment. The scene captures the innocence and imagination of childhood, with the toy ship's journey symbolizing endless adventures in a whimsical, indoor setting.</p>
                    </td>
                    <td style="width: 25%; vertical-align: top;">
                        <video src="https://github.com/user-attachments/assets/ea3af39a-3160-4999-90ec-2f7863c5b0e9" width="100%" controls autoplay></video>
                    </td>
                    <td style="width: 25%; vertical-align: top; font-size: 0.8em;">
                        <p>The camera follows behind a white vintage SUV with a black roof rack as it speeds up a steep dirt road surrounded by pine trees on a steep mountain slope, dust kicks up from its tires, the sunlight shines on the SUV as it speeds along the dirt road, casting a warm glow over the scene. The dirt road curves gently into the distance, with no other cars or vehicles in sight. The trees on either side of the road are redwoods, with patches of greenery scattered throughout. The car is seen from the rear following the curve with ease, making it seem as if it is on a rugged drive through the rugged terrain. The dirt road itself is surrounded by steep hills and mountains, with a clear blue sky above with wispy clouds.</p>
                    </td>
                    <td style="width: 25%; vertical-align: top;">
                        <video src="https://github.com/user-attachments/assets/9de41efd-d4d1-4095-aeda-246dd834e91d" width="100%" controls autoplay></video>
                    </td>
                </tr>
                <tr>
                    <td style="width: 25%; vertical-align: top; font-size: 0.8em;">
                        <p>A street artist, clad in a worn-out denim jacket and a colorful bandana, stands before a vast concrete wall in the heart, holding a can of spray paint, spray-painting a colorful bird on a mottled wall.</p>
                    </td>
                    <td style="width: 25%; vertical-align: top;">
                        <video src="https://github.com/user-attachments/assets/941d6661-6a8d-4a1b-b912-59606f0b2841" width="100%" controls autoplay></video>
                    </td>
                    <td style="width: 25%; vertical-align: top; font-size: 0.8em;">
                        <p>In the haunting backdrop of a war-torn city, where ruins and crumbled walls tell a story of devastation, a poignant close-up frames a young girl. Her face is smudged with ash, a silent testament to the chaos around her. Her eyes glistening with a mix of sorrow and resilience, capturing the raw emotion of a world that has lost its innocence to the ravages of conflict.</p>
                    </td>
                    <td style="width: 25%; vertical-align: top;">
                        <video src="https://github.com/user-attachments/assets/938529c4-91ae-4f60-b96b-3c3947fa63cb" width="100%" controls autoplay></video>
                    </td>
                </tr>
            </table>
            """)
        with gr.TabItem("video-to-video", id=1):
            with gr.Row():
                with gr.Column():
                    video = gr.Video(label="Driving Video")
                    strength2 = gr.Number(value=0.8, minimum=0.1, maximum=1.0, step=0.01, label="Strength")
                    prompt2 = gr.Textbox(label="Prompt (Less than 200 Words. The more detailed the better.)", placeholder="Enter your prompt here", lines=5)

                    with gr.Row():
                        gr.Markdown(
                            "✨ To enhance the prompt, either set the OPENAI_API_KEY variable from the Configure menu (if you have an OpenAI API key), or just use chatgpt to enhance the prompt manually (Recommended)",
                        )
                        enhance_button2 = gr.Button("✨ Enhance Prompt(Optional)")

                    with gr.Row():
                        model_choice2 = gr.Dropdown(["THUDM/CogVideoX-2b", "THUDM/CogVideoX-5b"], value="THUDM/CogVideoX-2b", label="Model")
                    with gr.Row():
                        num_inference_steps2 = gr.Number(label="Inference Steps", value=50)
                        guidance_scale2 = gr.Number(label="Guidance Scale", value=6.0)
                    with gr.Row():
                        dtype_choice2 = gr.Radio(["bfloat16", "float16"], label="dtype (older machines may not support bfloat16. try float16 if bfloat16 doesn't work)", value="bfloat16")
                    with gr.Row():
                        seed_param2 = gr.Number(
                            label="Inference Seed (Enter a positive number, -1 for random)", value=-1
                        )
                    with gr.Row():
                        enable_scale2 = gr.Checkbox(label="Super-Resolution (720 × 480 -> 2880 × 1920)", value=False)
                        enable_rife2 = gr.Checkbox(label="Frame Interpolation (8fps -> 16fps)", value=False)
                    generate_button2 = gr.Button("🎬 Generate Video")

                with gr.Column():
                    video_output2 = gr.Video(label="CogVideoX Generate Video", width=720, height=480)
                    with gr.Row():
                        download_video_button2 = gr.File(label="📥 Download Video", visible=False)
                        download_gif_button2 = gr.File(label="📥 Download GIF", visible=False)
                        send_to_vid2vid_button2 = gr.Button("Send to video-to-video", visible=False)
        with gr.TabItem("image-to-video", id=2):
            with gr.Row():
                with gr.Column():
                    image = gr.Image(label="Driving Image")
                    strength3 = gr.Number(value=0.8, minimum=0.1, maximum=1.0, step=0.01, label="Strength")
                    prompt3 = gr.Textbox(label="Prompt (Less than 200 Words. The more detailed the better.)", placeholder="Enter your prompt here", lines=5)

                    with gr.Row():
                        gr.Markdown(
                            "✨ To enhance the prompt, either set the OPENAI_API_KEY variable from the Configure menu (if you have an OpenAI API key), or just use chatgpt to enhance the prompt manually (Recommended)",
                        )
                        enhance_button3 = gr.Button("✨ Enhance Prompt(Optional)")

                    with gr.Row():
                        model_choice3 = gr.Dropdown(["THUDM/CogVideoX-2b", "THUDM/CogVideoX-5b"], value="THUDM/CogVideoX-2b", label="Model")
                    with gr.Row():
                        num_inference_steps3 = gr.Number(label="Inference Steps", value=50)
                        guidance_scale3 = gr.Number(label="Guidance Scale", value=6.0)
                    with gr.Row():
                        dtype_choice3 = gr.Radio(["bfloat16", "float16"], label="dtype (older machines may not support bfloat16. try float16 if bfloat16 doesn't work)", value="bfloat16")
                    with gr.Row():
                        seed_param3 = gr.Number(
                            label="Inference Seed (Enter a positive number, -1 for random)", value=-1
                        )
                    with gr.Row():
                        enable_scale3 = gr.Checkbox(label="Super-Resolution (720 × 480 -> 2880 × 1920)", value=False)
                        enable_rife3 = gr.Checkbox(label="Frame Interpolation (8fps -> 16fps)", value=False)
                    generate_button3 = gr.Button("🎬 Generate Video")

                with gr.Column():
                    video_output3 = gr.Video(label="CogVideoX Generate Video", width=720, height=480)
                    with gr.Row():
                        download_video_button3 = gr.File(label="📥 Download Video", visible=False)
                        download_gif_button3 = gr.File(label="📥 Download GIF", visible=False)
                        send_to_vid2vid_button3 = gr.Button("Send to video-to-video", visible=False)

    def generate(
        prompt,
        image_input,
        video_input,
        video_strength,
        num_inference_steps,
        guidance_scale,
        model_choice,
        dtype,
        seed_value,
        scale_status,
        rife_status,
        progress=gr.Progress(track_tqdm=True)
    ):

        latents, seed = infer(
            model_choice,
            prompt,
            image_input,
            video_input,
            video_strength,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            seed=seed_value,
            progress=progress,
        )
        if scale_status:
            latents = utils.upscale_batch_and_concatenate(upscale_model, latents, device)
        if rife_status:
            latents = rife_inference_with_latents(frame_interpolation_model, latents)

        batch_size = latents.shape[0]
        batch_video_frames = []
        for batch_idx in range(batch_size):
            pt_image = latents[batch_idx]
            pt_image = torch.stack([pt_image[i] for i in range(pt_image.shape[0])])

            image_np = VaeImageProcessor.pt_to_numpy(pt_image)
            image_pil = VaeImageProcessor.numpy_to_pil(image_np)
            batch_video_frames.append(image_pil)

        video_path = utils.save_video(batch_video_frames[0], fps=math.ceil((len(batch_video_frames[0]) - 1) / 6))
        video_update = gr.update(visible=True, value=video_path)
        gif_path = convert_to_gif(video_path)
        gif_update = gr.update(visible=True, value=gif_path)
        seed_update = gr.update(visible=True, value=seed)
        vid2vid_update = gr.update(visible=True)

        return video_path, video_update, gif_update, seed_update, vid2vid_update

    def enhance_prompt_func(prompt):
        return convert_prompt(prompt, retry_times=1)

    def send_to_vid2vid(vid):
        vid2vid = gr.update(value=vid)
        tabs = gr.Tabs(selected=1)
        return [vid2vid, tabs]

    generate_button.click(
        generate,
        inputs=[prompt, None, None, strength, num_inference_steps, guidance_scale, model_choice, dtype_choice, seed_value, scale_status, rife_status],
        outputs=[video_output, download_video_button, download_gif_button, send_to_vid2vid_button],
    )
    generate_button2.click(
        generate,
        inputs=[prompt2, None, video, strength2, num_inference_steps2, guidance_scale2, model_choice2, dtype_choice2, seed_value2, scale_status2, rife_status2],
        outputs=[video_output2, download_video_button2, download_gif_button2, send_to_vid2vid_button2],
    )
    generate_button3.click(
        generate,
        inputs=[prompt3, image, None, strength3, num_inference_steps3, guidance_scale3, model_choice3, dtype_choice3, seed_value3, scale_status3, rife_status3],
        outputs=[video_output3, download_video_button3, download_gif_button3, send_to_vid2vid_button3],
    )

    enhance_button.click(enhance_prompt_func, inputs=[prompt], outputs=[prompt])
    enhance_button2.click(enhance_prompt_func, inputs=[prompt2], outputs=[prompt2])
    enhance_button3.click(enhance_prompt_func, inputs=[prompt2], outputs=[prompt2])

    send_to_vid2vid_button.click(send_to_vid2vid, inputs=[video_output], outputs=[video, tabs])
    send_to_vid2vid_button2.click(send_to_vid2vid, inputs=[video_output], outputs=[video, tabs])
    send_to_vid2vid_button3click(send_to_vid2vid, inputs=[video_output], outputs=[video, tabs])

if __name__ == "__main__":
    demo.launch()
