"""Drive ComfyUI to turn (image, script) pairs into MP4s via the LTX-2.3 stack.

Mirrors generate_videos.generate_all() signature so the orchestrator and webapp
can swap providers transparently.

Reuses a workflow saved in ComfyUI's userdata (default: ltx23_nerdy_rodent).
At submit time we override these nodes from the script's data:
  - The positive CLIPTextEncode prompt (uses action_script when present, else
    hindi_script — same pattern as the Grok provider's _build_grok_prompt)
  - The SaveVideo filename so each clip lands as vid_NN_<slug>.mp4
  - The seed for reproducibility (derived from the run + slot index)
  - The output dimensions / length default to 9:16 vertical at 10 seconds

Note: this is currently TEXT-to-video. The per-script Gemini image (used by the
Grok provider as a first-frame reference) is NOT consumed here yet. For true
image-to-video, change COMFYUI_WORKFLOW in Settings to an I2V variant whose
LoadImage node id is exposed via the COMFYUI_IMAGE_NODE_ID env var.

CLI usage matches steps/generate_videos.py for drop-in substitution:
    python3.13 steps/generate_videos_comfyui.py <run_dir> [--only 1 2]
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
# Engine:
#   "ltx"     – LTX-2.3 v1.1 diffusion (Pixar-style via STYLE prompt, no lip-sync)
#   "wan"     – Wan 2.2 TI2V 5B Turbo Q8 (fast, image-conditioned, silent → audio overlay)
#   "wan_s2v" – Wan 2.2 S2V 14B Q3 GGUF (audio-conditioned, real lip-sync, slower)
# Each engine has its own saved workflow slug.
COMFYUI_ENGINE = os.environ.get("COMFYUI_ENGINE", "ltx")
_DEFAULT_WORKFLOW = {
    "ltx": "ltx23_nerdy_rodent",
    "wan": "wan22_s2v_object_talk",
    "wan_s2v": "wan22_s2v_14b_q3_object_talk",
}.get(COMFYUI_ENGINE, "ltx23_nerdy_rodent")
COMFYUI_WORKFLOW = os.environ.get("COMFYUI_WORKFLOW") or _DEFAULT_WORKFLOW
# We pull the rendered MP4 over HTTP via ComfyUI's /view endpoint — works
# transparently whether ComfyUI runs on localhost or on a remote box.

# Image-to-video: when an img_NN_<slug>.png exists for the script, upload it
# to ComfyUI and inject a LoadImage + LTXVImgToVideo pair so the image becomes
# the first frame of the rendered clip. Set COMFYUI_I2V=0 to force pure T2V.
COMFYUI_I2V = os.environ.get("COMFYUI_I2V", "1") not in ("0", "false", "False", "")
# Conditioning strength for the image. 1.0 = rigidly pin first frame (often
# causes "great frame 1-2 then video degrades"); 0.6-0.8 = image as soft guide,
# better motion coherence at the cost of a slightly less literal first frame.
COMFYUI_I2V_STRENGTH = float(os.environ.get("COMFYUI_I2V_STRENGTH", "0.7"))
# When set, the pipeline runs in text-only mode (Gemini images not generated).
# We then build a richer prompt that includes the character description so
# LTX can imagine the character from text alone.
SKIP_IMAGES = os.environ.get("SKIP_IMAGES", "0") not in ("0", "false", "False", "")

# Extra LoRA: VBVR ("Video Reasoning") — improves prompt following + temporal
# consistency. Empty string disables. The LoRA is chained between the base UNet
# and ALL downstream consumers (stage-1 CFGGuider + the distillation LoRA),
# so it influences both sampling stages.
COMFYUI_VBVR_LORA = os.environ.get("COMFYUI_VBVR_LORA", "VBVR-official-comfyui.safetensors")
COMFYUI_VBVR_STRENGTH = float(os.environ.get("COMFYUI_VBVR_STRENGTH", "0.7"))

# Node IDs in the saved workflow — these match the ltx23_nerdy_rodent graph we
# tested. Override per-workflow via env vars if your custom workflow differs.
NODE_POSITIVE_PROMPT = int(os.environ.get("COMFYUI_NODE_POSITIVE", "5"))
NODE_NEGATIVE_PROMPT = int(os.environ.get("COMFYUI_NODE_NEGATIVE", "6"))
NODE_EMPTY_IMAGE = int(os.environ.get("COMFYUI_NODE_DIMS", "7"))
NODE_LENGTH = int(os.environ.get("COMFYUI_NODE_LENGTH", "10"))
NODE_NOISE_PRIMARY = int(os.environ.get("COMFYUI_NODE_NOISE", "16"))
NODE_SAVE_VIDEO = int(os.environ.get("COMFYUI_NODE_SAVE", "38"))

# Resolution + length defaults per NerdyRodent's tested 9:16 sweet spot
DEFAULT_WIDTH = int(os.environ.get("COMFYUI_WIDTH", "720"))
DEFAULT_HEIGHT = int(os.environ.get("COMFYUI_HEIGHT", "1280"))
DEFAULT_FRAMES = int(os.environ.get("COMFYUI_FRAMES", "241"))  # 10s @ 24fps, (n-1)%8==0

POLL_INTERVAL_S = 5
GEN_TIMEOUT_S = 3600  # 60 min — Wan S2V can be slow at full res on a 40GB GPU


def _slug(name: str) -> str:
    return "-".join(name.lower().split())[:30]


# ---------- ComfyUI HTTP helpers ----------

def _http_get(path: str, timeout: int = 10) -> dict:
    url = f"{COMFYUI_URL}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _http_post(path: str, payload: dict, timeout: int = 30) -> dict:
    url = f"{COMFYUI_URL}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # Surface the response body so prompt-validation errors are useful
        body = e.read().decode("utf-8", errors="ignore")[:1200]
        raise RuntimeError(f"ComfyUI {e.code}: {body}") from None


def _load_workflow_api_format(name: str) -> dict:
    """Fetch a saved workflow from ComfyUI userdata and convert GUI format → API format.

    The /userdata endpoint returns the GUI graph (nodes + links). The /prompt
    endpoint needs API format ({node_id: {class_type, inputs}}). We do the
    minimal conversion here using the link table to rewire inputs.
    """
    path = f"/userdata/workflows%2F{urllib.parse.quote(name + '.json', safe='')}"
    try:
        wf = _http_get(path, timeout=15)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"workflow '{name}' not found at {COMFYUI_URL}{path} (HTTP {e.code}). "
            f"Open ComfyUI and save the workflow with that name, or set COMFYUI_WORKFLOW."
        ) from e

    # Detect format: API workflows are dicts keyed by node-id strings; GUI workflows have "nodes" list
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        return _gui_to_api(wf)
    return wf


def _gui_to_api(gui: dict) -> dict:
    """Convert ComfyUI GUI graph format → API prompt format.

    GUI format:   {nodes: [{id, type, widgets_values, inputs:[{name,link}]}], links: [[id,from_node,from_slot,to_node,to_slot,type]]}
    API format:   {<node_id>: {class_type, inputs: {<input_name>: <value_or_[from_node_str, slot]>}}}
    """
    # Build link lookup: link_id -> [from_node_id, from_slot]
    link_src: dict[int, list] = {}
    for link in gui.get("links", []):
        # link = [link_id, from_node, from_slot, to_node, to_slot, type]
        link_src[link[0]] = [str(link[1]), link[2]]

    out: dict = {}
    for n in gui.get("nodes", []):
        # Skip non-runnable utility nodes (Reroute, Note, PreviewAny)
        if n.get("type") in ("Reroute", "Note", "PreviewAny", "MarkdownNote"):
            # Reroute passes through — we resolve by walking the link graph below.
            continue

        node_id = str(n["id"])
        class_type = n["type"]
        inputs: dict = {}

        # Widget values become named inputs based on the node's widget order.
        # We need to know the widget *names* — the GUI dict has those in
        # n["widgets_values"] but the names live in the node class definition.
        # As a pragma, we ask ComfyUI for object_info once per class encountered
        # and use it to map widget index → name.
        widget_names = _get_widget_names(class_type)
        widgets = n.get("widgets_values", []) or []
        for i, val in enumerate(widgets):
            if i < len(widget_names):
                inputs[widget_names[i]] = val

        # Wire link inputs (these take precedence — and most importantly, named
        # widget inputs that also have a link in the GUI must become the link
        # tuple, not the widget value).
        for inp in n.get("inputs", []) or []:
            link_id = inp.get("link")
            if link_id is None:
                continue
            src = link_src.get(link_id)
            if src is None:
                continue
            # Walk through Reroute nodes to find the real source
            src = _resolve_through_reroutes(src, gui, link_src)
            inputs[inp["name"]] = src

        out[node_id] = {"class_type": class_type, "inputs": inputs}
    return out


def _resolve_through_reroutes(src: list, gui: dict, link_src: dict) -> list:
    """If `src` points at a Reroute node, follow its single input back to the real source."""
    seen = set()
    while True:
        from_node_id = int(src[0])
        if from_node_id in seen:
            return src  # cycle guard
        seen.add(from_node_id)
        node = next((n for n in gui["nodes"] if n["id"] == from_node_id), None)
        if not node or node.get("type") != "Reroute":
            return src
        # Reroute has one input — follow its link back
        inputs = node.get("inputs", [])
        if not inputs:
            return src
        link_id = inputs[0].get("link")
        if link_id is None:
            return src
        next_src = link_src.get(link_id)
        if next_src is None:
            return src
        src = next_src


_widget_name_cache: dict[str, list[str]] = {}


def _is_widget_type(spec) -> bool:
    """Decide whether a /object_info input spec should consume a value from widgets_values.

    ComfyUI's frontend renders an input as a widget when its first element is:
      - "INT" | "FLOAT" | "STRING" | "BOOLEAN" — primitive value types
      - a list  — combo dropdown (the list IS the options)
    Connection-only inputs have a CAPS class-name string like "IMAGE", "LATENT",
    "MODEL", "CLIP", "VAE", "CONDITIONING", "SAMPLER", "SIGMAS", "GUIDER",
    "NOISE", "AUDIO", "VIDEO", "MASK", "LATENT_UPSCALE_MODEL", etc.
    """
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    first = spec[0]
    if isinstance(first, list):
        return True
    if first in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"):
        return True
    return False


def _get_widget_names(class_type: str) -> list[str]:
    """Return ordered list of widget-typed input names for a node class.

    Only inputs that the GUI renders as widgets are returned — connection-only
    inputs (IMAGE/LATENT/MODEL/etc) are filtered out so widget_values lines up.
    """
    if class_type in _widget_name_cache:
        return _widget_name_cache[class_type]
    try:
        info = _http_get(f"/object_info/{urllib.parse.quote(class_type)}", timeout=10)
    except Exception:
        _widget_name_cache[class_type] = []
        return []
    schema = info.get(class_type, {}).get("input", {})
    names: list[str] = []
    for section in ("required", "optional"):
        for name, spec in (schema.get(section) or {}).items():
            if _is_widget_type(spec):
                names.append(name)
    _widget_name_cache[class_type] = names
    return names


# ---------- Image upload (for image-to-video) ----------

def _upload_image_to_comfyui(image_path: Path) -> str:
    """POST the image to ComfyUI /upload/image. Returns the filename ComfyUI saved it as.

    Works over the network — no shared filesystem required between this process
    and the ComfyUI host.
    """
    import mimetypes
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    boundary = f"----otp-{uuid.uuid4().hex}"
    body_parts: list[bytes] = []
    # multipart 'image' field
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'.encode())
    body_parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    body_parts.append(image_path.read_bytes())
    body_parts.append(b"\r\n")
    # overwrite=true so multiple runs with the same slot reuse the slot
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b'Content-Disposition: form-data; name="overwrite"\r\n\r\n')
    body_parts.append(b"true\r\n")
    body_parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(body_parts)
    req = urllib.request.Request(
        f"{COMFYUI_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    name = resp.get("name")
    if not name:
        raise RuntimeError(f"ComfyUI /upload/image: no 'name' in response: {resp}")
    return name


# Node IDs we add when patching for I2V. Use values well outside the existing
# 1-49 range used by ltx23_nerdy_rodent so we never collide.
NODE_LOAD_IMAGE = "100"
NODE_IMG_TO_VIDEO = "101"


def _inject_i2v(prompt: dict, uploaded_image_name: str) -> None:
    """Mutate `prompt` (API-format dict) to add image-to-video conditioning.

    Pattern (drop-in replacement for the empty-latent T2V path):
      LoadImage(uploaded_image_name) ─┐
      LTXVConditioning ───── pos/neg ─┼─► LTXVImgToVideo ─► pos'/neg'/latent'
      VideoVAE ─────────────────── ───┘                          │
                                                                  ├─► CFGGuider (replaces direct LTXVConditioning)
                                                                  ├─► LTXVCropGuides (replaces direct LTXVConditioning)
                                                                  └─► LTXVConcatAVLatent.video_latent
                                                                      (replaces EmptyLTXVLatentVideo output)

    Affected existing nodes (by ID, matching ltx23_nerdy_rodent):
      18  CFGGuider          — positive/negative inputs rewired
      21  LTXVCropGuides     — positive/negative inputs rewired
      15  LTXVConcatAVLatent — video_latent input rewired
    """
    # Find existing node IDs by class_type so this also works if the workflow
    # was edited and IDs shifted. We use the class name as the anchor.
    def _find(class_type: str) -> str | None:
        for nid, node in prompt.items():
            if isinstance(node, dict) and node.get("class_type") == class_type:
                return nid
        return None

    cond_id = _find("LTXVConditioning")
    cfg_guider_id = _find("CFGGuider")  # first one — stage 1 sampler
    crop_guides_id = _find("LTXVCropGuides")
    # LTXVConcatAVLatent appears twice in the nerdy_rodent workflow (stage 1 + stage 2).
    # We rewire the FIRST one which is what feeds the stage-1 sampler.
    concat_id = _find("LTXVConcatAVLatent")
    # VAE — find the video VAE specifically (audio_vae filename has 'audio_vae')
    vae_id = None
    for nid, node in prompt.items():
        if isinstance(node, dict) and node.get("class_type") == "VAELoaderKJ":
            name = node.get("inputs", {}).get("vae_name", "")
            if "video_vae" in name:
                vae_id = nid
                break
    if not all([cond_id, cfg_guider_id, crop_guides_id, concat_id, vae_id]):
        # If any anchor is missing, the workflow doesn't match what we expect.
        # Fall through to T2V (the caller already submitted prompt without injection).
        print("  [i2v] required anchor node missing; falling back to T2V "
              f"(cond={cond_id} cfg={cfg_guider_id} crop={crop_guides_id} "
              f"concat={concat_id} vae={vae_id})", flush=True)
        return

    # Insert LoadImage
    prompt[NODE_LOAD_IMAGE] = {
        "class_type": "LoadImage",
        "inputs": {"image": uploaded_image_name},
        "_meta": {"title": "LoadImage (I2V first frame)"},
    }
    # Insert LTXVImgToVideo
    prompt[NODE_IMG_TO_VIDEO] = {
        "class_type": "LTXVImgToVideo",
        "inputs": {
            "positive": [cond_id, 0],
            "negative": [cond_id, 1],
            "vae": [vae_id, 0],
            "image": [NODE_LOAD_IMAGE, 0],
            "width": DEFAULT_WIDTH,
            "height": DEFAULT_HEIGHT,
            "length": DEFAULT_FRAMES,
            "batch_size": 1,
            "strength": COMFYUI_I2V_STRENGTH,
        },
        "_meta": {"title": "LTXVImgToVideo (I2V)"},
    }
    # Rewire: CFGGuider takes positive/negative from LTXVImgToVideo instead of LTXVConditioning
    prompt[cfg_guider_id]["inputs"]["positive"] = [NODE_IMG_TO_VIDEO, 0]
    prompt[cfg_guider_id]["inputs"]["negative"] = [NODE_IMG_TO_VIDEO, 1]
    # Same for LTXVCropGuides (the refinement-stage conditioner)
    prompt[crop_guides_id]["inputs"]["positive"] = [NODE_IMG_TO_VIDEO, 0]
    prompt[crop_guides_id]["inputs"]["negative"] = [NODE_IMG_TO_VIDEO, 1]
    # LTXVConcatAVLatent.video_latent now comes from LTXVImgToVideo (slot 2)
    # instead of EmptyLTXVLatentVideo. This is the seeded first-frame latent.
    prompt[concat_id]["inputs"]["video_latent"] = [NODE_IMG_TO_VIDEO, 2]


# Node ID for the injected VBVR LoRA — same out-of-range strategy as I2V nodes.
NODE_VBVR_LORA = "102"


def _inject_vbvr_lora(prompt: dict, lora_name: str, strength: float) -> None:
    """Insert a VBVR (Video Reasoning) LoRA between the base UNet and all
    downstream model consumers, so it influences BOTH sampling stages.

    Topology change:
       UNetLoader ──▶ [CFGGuider stage1, LoraLoaderModelOnly(distillation)]
       becomes:
       UNetLoader ──▶ LoraLoader(VBVR) ──▶ [CFGGuider stage1, LoraLoader(distillation)]

    We anchor by class name so this works with both the GGUF and native loaders.
    """
    # Find the UNet (either UnetLoaderGGUF or UNETLoader)
    unet_id = None
    for nid, node in prompt.items():
        ct = node.get("class_type") if isinstance(node, dict) else None
        if ct in ("UnetLoaderGGUF", "UNETLoader"):
            unet_id = nid
            break
    if unet_id is None:
        print("  [vbvr] no UNet loader found — skipping LoRA injection", flush=True)
        return

    # Find every node currently consuming the UNet's model output and redirect
    # them at our VBVR LoRA instead. Typical consumers in the nerdy_rodent
    # workflow are CFGGuider (stage 1) and LoraLoaderModelOnly (distillation).
    consumers: list[str] = []
    for nid, node in prompt.items():
        if nid == NODE_VBVR_LORA or not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        for key, val in list(inputs.items()):
            if isinstance(val, list) and len(val) == 2 and val[0] == unet_id and val[1] == 0:
                # This input pulls from UNet's MODEL slot — most likely a
                # model-typed input (model, MODEL). Redirect to our new LoRA.
                if key in ("model",):
                    inputs[key] = [NODE_VBVR_LORA, 0]
                    consumers.append(f"{nid}.{key}")
    if not consumers:
        print(f"  [vbvr] no model consumers of UNet '{unet_id}' found — skipping", flush=True)
        return

    # Insert the VBVR LoRA node, feeding from the original UNet
    prompt[NODE_VBVR_LORA] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "lora_name": lora_name,
            "strength_model": strength,
            "model": [unet_id, 0],
        },
        "_meta": {"title": f"VBVR LoRA ({strength})"},
    }
    print(f"  [vbvr] chained {lora_name} @ {strength} (consumers: {', '.join(consumers)})",
          flush=True)


# ---------- Prompt building ----------

STYLE_GUARD = (
    "STYLE (maintain throughout every single frame, no drift):\n"
    "Pixar-style 3D animated cartoon character — NOT a human, NOT a person. "
    "Keep the personified object's exact look, proportions, eyes, mouth and "
    "color scheme stable across all frames. Soft cinematic lighting, shallow "
    "depth of field. The character is an animated OBJECT with a face — do not "
    "morph it into a human figure, human body, or realistic person."
)


def _build_prompt_text(s: dict, include_character: bool = True) -> str:
    """Build the LTX positive prompt for one clip.

    We ALWAYS include:
      - STYLE  — anchors the Pixar cartoon look so LTX doesn't drift to a human
      - SUBJECT — the character's visual description from image_prompt
      - ACTION — camera + motion
      - DIALOGUE — verbatim Hindi script for lip-sync

    Even in I2V mode the model tends to lose the cartoon style after a few
    frames if the prompt doesn't explicitly carry the subject + style — so we
    inject both regardless of whether the image is being used as frame 1.
    """
    dialogue = (s.get("hindi_script") or "").strip()
    action = (s.get("action_script") or "").strip()
    character = (s.get("image_prompt") or "").strip() if include_character else ""

    parts = [STYLE_GUARD]
    if character:
        parts.append(
            "SUBJECT (the visual character — render this exactly):\n"
            f"{character}"
        )
    if action:
        parts.append(
            "ACTION (camera + character + scenery motion during the clip):\n"
            f"{action}"
        )
    if dialogue:
        parts.append(
            "DIALOGUE (the character speaks this verbatim in Hindi, lip-sync to it):\n"
            f"{dialogue}"
        )
    return "\n\n".join(parts)


def _patch_workflow_ltx(wf: dict, prompt_text: str, out_filename_prefix: str, seed: int) -> dict:
    """Inject per-clip parameters for an LTX workflow."""
    p = copy.deepcopy(wf)

    def _set(node_id: int, key: str, value) -> None:
        n = p.get(str(node_id))
        if n is None:
            return
        n["inputs"][key] = value

    # Positive prompt — sometimes wired via a link from a TextGenerateLTX2Prompt
    # node. Overwriting inputs.text wins over the (now-unused) link.
    pos = p.get(str(NODE_POSITIVE_PROMPT))
    if pos is not None:
        pos["inputs"]["text"] = prompt_text
    _set(NODE_EMPTY_IMAGE, "width", DEFAULT_WIDTH)
    _set(NODE_EMPTY_IMAGE, "height", DEFAULT_HEIGHT)
    _set(NODE_LENGTH, "value", DEFAULT_FRAMES)
    _set(NODE_NOISE_PRIMARY, "noise_seed", seed)
    _set(NODE_SAVE_VIDEO, "filename_prefix", out_filename_prefix)
    return p


def _patch_workflow_wan(wf: dict, prompt_text: str, dialogue_text: str,
                        out_filename_prefix: str, seed: int) -> dict:
    """Inject per-clip parameters for a Wan 2.2 workflow (S2V or TI2V).

    Anchor nodes by class_type (so this works for either topology):
      F5TTSAudio                                  → speech = hindi_script + seed
      KSampler                                    → seed
      KSampler.positive (→ CLIPTextEncode)        → text = full prompt
      Wan22ImageToVideoLatent OR WanSoundImageToVideo
                                                  → width/height/length
      VHS_VideoCombine                            → filename_prefix
    """
    p = copy.deepcopy(wf)

    def _find_first(class_type: str) -> str | None:
        for nid, node in p.items():
            if isinstance(node, dict) and node.get("class_type") == class_type:
                return nid
        return None

    # 1. F5-TTS speech for the audio track (only if workflow uses it; silent
    # workflows that overlay audio post-merge omit this node).
    # NOTE: F5-TTS with English voice + Hindi text can produce NaN audio that
    # ffmpeg's AAC encoder rejects → all clips fail at VHS_VideoCombine. The
    # safe default workflow now SKIPS audio in VHS and lets the pipeline
    # handle audio overlay separately.
    f5 = _find_first("F5TTSAudio")
    if f5 and dialogue_text:
        p[f5]["inputs"]["speech"] = dialogue_text
        p[f5]["inputs"]["seed"] = seed

    # 2. KSampler is the single point that always knows the positive conditioning,
    # regardless of whether the workflow is TI2V (Wan22ImageToVideoLatent)
    # or S2V (WanSoundImageToVideo). Walk from KSampler.positive → CLIPTextEncode.
    ks = _find_first("KSampler")
    if ks:
        p[ks]["inputs"]["seed"] = seed
        pos_link = p[ks]["inputs"].get("positive")
        if isinstance(pos_link, list) and len(pos_link) == 2:
            pos_id = pos_link[0]
            # If positive feeds from WanSoundImageToVideo (S2V topology), step
            # one more hop back to find the CLIPTextEncode behind it.
            if pos_id in p and p[pos_id].get("class_type") == "WanSoundImageToVideo":
                inner = p[pos_id]["inputs"].get("positive")
                if isinstance(inner, list) and len(inner) == 2:
                    pos_id = inner[0]
            if pos_id in p and p[pos_id].get("class_type") == "CLIPTextEncode":
                p[pos_id]["inputs"]["text"] = prompt_text

    # 3. Dimensions + length on whichever latent node the workflow uses
    for ct in ("Wan22ImageToVideoLatent", "WanSoundImageToVideo", "WanImageToVideo"):
        nid = _find_first(ct)
        if nid:
            p[nid]["inputs"]["width"] = int(os.environ.get("COMFYUI_WAN_WIDTH", "480"))
            p[nid]["inputs"]["height"] = int(os.environ.get("COMFYUI_WAN_HEIGHT", "832"))
            # Wan22 needs length % 4 == 0 + 1 (per its schema step=4 default 49).
            # Default 121 = 5 sec @ 24 fps. Override via env if needed.
            p[nid]["inputs"]["length"] = int(os.environ.get("COMFYUI_WAN_FRAMES", "121"))
            break

    # 4. Output filename
    save = _find_first("VHS_VideoCombine")
    if save:
        p[save]["inputs"]["filename_prefix"] = out_filename_prefix
    return p


def _patch_workflow(wf: dict, prompt_text: str, out_filename_prefix: str, seed: int,
                    dialogue_text: str = "") -> dict:
    """Dispatch to the engine-specific patcher based on COMFYUI_ENGINE.

    Both Wan TI2V (silent → overlay) and Wan S2V (audio-conditioned) share the
    same _patch_workflow_wan because it anchors by class_type and handles
    either Wan22ImageToVideoLatent or WanSoundImageToVideo automatically.
    Audio injection for S2V happens later in generate_one (LoadAudio patch).
    """
    if COMFYUI_ENGINE in ("wan", "wan_s2v"):
        return _patch_workflow_wan(wf, prompt_text, dialogue_text, out_filename_prefix, seed)
    return _patch_workflow_ltx(wf, prompt_text, out_filename_prefix, seed)


# ---------- Queue + polling ----------

def _queue_prompt(prompt_dict: dict, client_id: str) -> str:
    resp = _http_post("/prompt", {"prompt": prompt_dict, "client_id": client_id})
    if resp.get("node_errors"):
        # Surface the first error compactly so the orchestrator's log makes sense
        first = next(iter(resp["node_errors"].items()), None)
        raise RuntimeError(f"ComfyUI rejected prompt: {first}")
    pid = resp.get("prompt_id")
    if not pid:
        raise RuntimeError(f"ComfyUI returned no prompt_id: {resp}")
    return pid


def _wait_for_completion(prompt_id: str, timeout_s: int = GEN_TIMEOUT_S) -> dict:
    """Block until /history/<pid> shows the run completed. Returns the history entry."""
    deadline = time.time() + timeout_s
    last_log = ""
    while time.time() < deadline:
        try:
            hist = _http_get(f"/history/{prompt_id}", timeout=10)
        except Exception:
            hist = {}
        if hist and prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {}).get("status_str")
            if status == "success":
                return entry
            if status == "error":
                msgs = entry.get("status", {}).get("messages", [])
                tail = "; ".join(str(m)[:200] for m in msgs[-3:])
                raise RuntimeError(f"ComfyUI run failed: {tail}")
        try:
            q = _http_get("/queue", timeout=10)
            running = len(q.get("queue_running", []))
            pending = len(q.get("queue_pending", []))
            line = f"comfyui: running={running} pending={pending} elapsed={int(time.time()-deadline+timeout_s)}s"
            if line != last_log:
                print(f"  {line}", flush=True)
                last_log = line
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"ComfyUI run {prompt_id} did not finish within {timeout_s}s")


def _extract_output_ref(entry: dict) -> tuple[str, str]:
    """Return (filename, subfolder) of the rendered MP4 from a history entry."""
    for _node_id, out in entry.get("outputs", {}).items():
        for clip in out.get("gifs", []) or []:  # ComfyUI uses 'gifs' for animated outputs
            fn = clip.get("filename")
            if fn:
                return fn, clip.get("subfolder", "")
        for clip in out.get("images", []) or []:  # some workflows save under 'images'
            fn = clip.get("filename", "")
            if fn.endswith(".mp4"):
                return fn, clip.get("subfolder", "")
    raise RuntimeError("no .mp4 output found in history entry")


# ---------- Post-gen audio overlay ----------

def _audio_duration_s(path: Path) -> float:
    """ffprobe-based duration in seconds. Returns 0.0 if it fails."""
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _edge_tts_once(text: str, voice: str, rate: str, out_path: Path) -> bool:
    """One Edge TTS attempt at a given rate."""
    try:
        import asyncio, edge_tts
        async def _run():
            com = edge_tts.Communicate(text, voice, rate=rate)
            await com.save(str(out_path))
        asyncio.run(_run())
        return out_path.exists() and out_path.stat().st_size > 100
    except Exception as e:
        print(f"  · Edge TTS failed at rate={rate}: {e}", flush=True)
        return False


def _generate_hindi_audio(text: str, out_path: Path,
                          target_max_s: float | None = None) -> bool:
    """Synthesise Hindi audio via Microsoft Edge TTS, optionally constrained
    to fit within `target_max_s` seconds.

    Strategy when target_max_s is given:
      1. First attempt at the user-configured base rate (default +0%).
      2. If audio is too long, retry at higher rates: +15%, +30%, +45%, +60%.
      3. If STILL too long, ffmpeg-atempo the final mp3 to fit exactly.
    """
    if not text.strip():
        return False
    voice = os.environ.get("COMFYUI_TTS_VOICE", "hi-IN-MadhurNeural")
    base_rate = os.environ.get("COMFYUI_TTS_RATE", "+0%")
    # Parse "+10%" → 10 so we can step up from there
    def _rate_str(pct: int) -> str:
        return f"+{pct}%" if pct >= 0 else f"{pct}%"
    try:
        base_pct = int(base_rate.rstrip("%"))
    except Exception:
        base_pct = 0

    attempts = [base_pct] + [base_pct + s for s in (15, 30, 45, 60) if base_pct + s <= 100]

    for pct in attempts:
        ok = _edge_tts_once(text, voice, _rate_str(pct), out_path)
        if not ok:
            continue
        if target_max_s is None:
            return True
        dur = _audio_duration_s(out_path)
        if dur > 0 and dur <= target_max_s:
            if pct != base_pct:
                print(f"  · TTS rate bumped to {_rate_str(pct)} to fit {target_max_s:.1f}s "
                      f"(audio={dur:.2f}s)", flush=True)
            return True
        if dur > target_max_s:
            print(f"  · TTS @ {_rate_str(pct)} = {dur:.2f}s > target {target_max_s:.1f}s, retrying faster", flush=True)

    # Last resort — compress the last successful mp3 to fit target via ffmpeg atempo
    if target_max_s and out_path.exists():
        dur = _audio_duration_s(out_path)
        if dur > target_max_s:
            ratio = dur / target_max_s  # >1
            import subprocess
            tmp = out_path.with_suffix(".compressed.mp3")
            # atempo is clamped to [0.5, 2.0] per filter — chain if ratio > 2
            atempo_chain = []
            r = ratio
            while r > 2.0:
                atempo_chain.append("atempo=2.0")
                r /= 2.0
            atempo_chain.append(f"atempo={r:.4f}")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_path),
                   "-filter:a", ",".join(atempo_chain), str(tmp)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                out_path.unlink()
                tmp.rename(out_path)
                print(f"  · ffmpeg atempo compressed audio to fit "
                      f"({dur:.2f}s × 1/{ratio:.2f} = {target_max_s:.1f}s)", flush=True)
                return True
            print(f"  · ffmpeg atempo failed: {res.stderr[-200:]}", flush=True)

    # Final fallback — gTTS (no rate control, but at least produces something)
    try:
        from gtts import gTTS
        gTTS(text=text, lang="hi", slow=False).save(str(out_path))
        print("  · fell back to gTTS (no rate control)", flush=True)
        return out_path.exists() and out_path.stat().st_size > 100
    except Exception as e:
        print(f"  · gTTS fallback also failed: {e}", flush=True)
        return False


def _merge_audio_into_video(silent_video: Path, audio_file: Path, out_path: Path) -> bool:
    """Use ffmpeg to overlay audio onto a silent mp4. Output replaces input.

    -shortest = stop at whichever is shorter (audio or video). For our 10-sec
    talking-character clips this trims to the audio length naturally.
    """
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(silent_video), "-i", str(audio_file),
        "-c:v", "copy",            # don't re-encode video
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  · ffmpeg merge failed: {r.stderr[-500:]}", flush=True)
        return False
    return out_path.exists() and out_path.stat().st_size > 1024


def _video_duration_s(path: Path) -> float:
    """Read video duration using cv2 (host ffprobe is broken by libopenh264
    missing-decoder errors on this system, even for metadata-only reads).
    cv2 reads the mp4 container metadata directly — works regardless.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        fc = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return float(fc / fps) if fps > 0 else 0.0
    except Exception:
        return 0.0


def _upload_file_to_comfyui(local_path: Path) -> str:
    """Upload an arbitrary file (mp4 / wav / mp3) to ComfyUI's input folder.
    Returns the filename ComfyUI saved it as. Reuses /upload/image — that
    endpoint accepts non-image files too and just puts them in input/.
    """
    import mimetypes
    mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    boundary = f"----otp-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="image"; filename="{local_path.name}"\r\n'.encode())
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(local_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n')
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{COMFYUI_URL}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    name = resp.get("name")
    if not name:
        raise RuntimeError(f"/upload/image returned no name: {resp}")
    return name


def _lip_sync_via_latentsync(video_path: Path, audio_path: Path,
                              lips_expression: float = 1.5,
                              inference_steps: int = 12) -> bool:
    """Run LatentSync on a silent video + audio file via ComfyUI's
    LatentSyncNode, replacing video_path with the lip-synced result.

    Returns True if the lip-sync succeeded, False on any error (caller
    keeps the original video — graceful degradation).

    Caveat: LatentSync uses YOLOFace for face detection and may fail on
    cartoon/object faces without recognizable human features. We catch the
    failure and leave the original video intact.
    """
    try:
        uploaded_video = _upload_file_to_comfyui(video_path)
        uploaded_audio = _upload_file_to_comfyui(audio_path)
    except Exception as e:
        print(f"  · lip-sync upload failed: {e}", flush=True)
        return False

    prompt = {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
                "video": uploaded_video,
                "force_rate": 0, "custom_width": 0, "custom_height": 0,
                "frame_load_cap": 0, "skip_first_frames": 0, "select_every_nth": 1}},
        "2": {"class_type": "LoadAudio", "inputs": {"audio": uploaded_audio}},
        "3": {"class_type": "LatentSyncNode", "inputs": {
                "images": ["1", 0],     # IMAGE frames from the silent mp4
                "audio": ["2", 0],
                "seed": 1247,
                "lips_expression": lips_expression,
                "inference_steps": inference_steps}},
        "4": {"class_type": "VHS_VideoCombine", "inputs": {
                "images": ["3", 0],
                "audio": ["3", 1],
                "frame_rate": 24.0, "loop_count": 0,
                "filename_prefix": f"object-talk/lipsync_{video_path.stem}",
                "format": "video/h264-mp4",
                "pingpong": False, "save_output": True}},
    }

    try:
        pid = _queue_prompt(prompt, str(uuid.uuid4()))
    except Exception as e:
        print(f"  · lip-sync queue failed: {e}", flush=True)
        return False

    print(f"  · lip-sync via LatentSync (prompt_id={pid[:8]}, steps={inference_steps})",
          flush=True)
    try:
        entry = _wait_for_completion(pid, timeout_s=600)
        filename, subfolder = _extract_output_ref(entry)
    except Exception as e:
        print(f"  · LatentSync failed (likely no face detected): {str(e)[:200]}", flush=True)
        return False

    tmp = video_path.parent / f".{video_path.stem}.lipsync.mp4"
    try:
        _fetch_output(filename, subfolder, tmp)
    except Exception as e:
        print(f"  · lip-sync download failed: {e}", flush=True)
        return False
    # Replace original
    video_path.unlink()
    tmp.rename(video_path)
    print(f"  · lip-synced ({video_path.stat().st_size // 1024} KB)", flush=True)
    return True


def _overlay_audio_on_silent_video(silent_video: Path, dialogue_text: str,
                                    pregen_audio: Path | None = None) -> None:
    """Overlay Hindi audio onto a silent video in place. If `pregen_audio` is
    supplied (e.g. the same wav that was fed into Wan S2V), reuse it instead
    of regenerating — this matters because the audio that conditioned the
    lip-sync must match the audio actually played, or the mouth desyncs.
    """
    if not dialogue_text and not pregen_audio:
        return
    import subprocess
    # Skip if the video already has an audio stream
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", str(silent_video)],
        capture_output=True, text=True,
    )
    if probe.stdout.strip():
        return  # already has audio

    vid_dur = _video_duration_s(silent_video)
    tmp_audio = silent_video.parent / f".{silent_video.stem}.audio.mp3"
    tmp_merged = silent_video.parent / f".{silent_video.stem}.withaudio.mp4"
    if pregen_audio and pregen_audio.exists():
        # Reuse the audio file Wan S2V was conditioned on
        import shutil as _sh
        _sh.copyfile(pregen_audio, tmp_audio)
        print(f"  · reusing S2V-conditioning audio ({tmp_audio.stat().st_size // 1024} KB)",
              flush=True)
    else:
        print(f"  · generating Hindi TTS audio (Edge TTS, neural voice, target ≤ {vid_dur:.1f}s)",
              flush=True)
        if not _generate_hindi_audio(dialogue_text, tmp_audio,
                                      target_max_s=vid_dur if vid_dur > 0 else None):
            print("  · TTS failed; leaving video silent", flush=True)
            return
    audio_dur = _audio_duration_s(tmp_audio)
    print(f"  · audio={audio_dur:.2f}s, video={vid_dur:.2f}s — merging", flush=True)
    if not _merge_audio_into_video(silent_video, tmp_audio, tmp_merged):
        return
    # Replace the silent video with the audio-overlaid version
    silent_video.unlink()
    tmp_merged.rename(silent_video)
    print(f"  · final size {silent_video.stat().st_size // 1024} KB (with audio)", flush=True)

    # Optional lip-sync post-step via LatentSync. Off by default because
    # LatentSync uses YOLOFace and may fail on cartoon/object faces — when
    # it fails we keep the audio-overlaid video unchanged (graceful skip).
    if os.environ.get("COMFYUI_LIPSYNC", "0") not in ("0", "false", "False", ""):
        steps = int(os.environ.get("COMFYUI_LIPSYNC_STEPS", "12"))
        expr = float(os.environ.get("COMFYUI_LIPSYNC_EXPR", "1.5"))
        _lip_sync_via_latentsync(silent_video, tmp_audio,
                                  lips_expression=expr, inference_steps=steps)
    tmp_audio.unlink(missing_ok=True)


def _fetch_output(filename: str, subfolder: str, out_path: Path) -> None:
    """Download the rendered MP4 from ComfyUI via /view (works over network)."""
    query = urllib.parse.urlencode({
        "filename": filename,
        "subfolder": subfolder,
        "type": "output",
    })
    url = f"{COMFYUI_URL}/view?{query}"
    req = urllib.request.Request(url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Stream to disk so big mp4s don't sit in RAM
    with urllib.request.urlopen(req, timeout=300) as resp, out_path.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            f.write(chunk)
    sz = out_path.stat().st_size
    if sz < 1024:
        raise RuntimeError(f"downloaded mp4 too small ({sz} bytes) — likely an error page")


# ---------- Per-item ----------

def _find_image_for_slot(out_dir: Path, slot: int) -> Path | None:
    """Locate the Gemini-generated image for this script slot (img_NN_*)."""
    matches = list(out_dir.glob(f"img_{slot:02d}_*"))
    return matches[0] if matches else None


def _collect_pending(scripts, out_dir: Path, only):
    pending = []
    for i, s in enumerate(scripts, 1):
        if only and i not in only:
            continue
        obj_slug = _slug(s["object"])
        out = out_dir / f"vid_{i:02d}_{obj_slug}.mp4"
        if out.exists() and out.stat().st_size > 1024:
            print(f"[{i}/5] {s['object']}: skip (already exists)", flush=True)
            continue
        pending.append((i, s, out))
    return pending


def generate_one(workflow_template: dict, script: dict, slot: int, out_path: Path,
                 image_path: Path | None = None) -> Path:
    # Always include the character description + style guard so the model
    # holds the Pixar cartoon look even when I2V is on (otherwise it drifts
    # to a human figure after frame 0).
    prompt_text = _build_prompt_text(script, include_character=True)
    dialogue_text = (script.get("hindi_script") or "").strip()
    # Stable per-slot seed: same script always uses same seed for reproducibility
    seed = (abs(hash(out_path.stem)) % (2**31)) or 1
    obj_slug = _slug(script["object"])
    prefix = f"object-talk/{out_path.parent.name}__vid_{slot:02d}_{obj_slug}"
    patched = _patch_workflow(workflow_template, prompt_text, prefix, seed,
                              dialogue_text=dialogue_text)

    pregen_audio_path: Path | None = None
    if COMFYUI_ENGINE in ("wan", "wan_s2v"):
        # Wan workflow already has a LoadImage node — upload the image (if any)
        # and patch the LoadImage filename directly.
        if image_path and image_path.exists():
            print(f"  · uploading {image_path.name} → ComfyUI for Wan ref_image", flush=True)
            uploaded = _upload_image_to_comfyui(image_path)
            for nid, node in patched.items():
                if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                    node["inputs"]["image"] = uploaded
                    break
            print(f"  · wan: ref_image set to {uploaded}", flush=True)
        else:
            print("  · wan: no image — workflow's LoadImage default will run", flush=True)
        # S2V-only: generate Hindi audio NOW (so it can condition the lip-sync),
        # upload it, patch LoadAudio, and resize length to match audio duration.
        if COMFYUI_ENGINE == "wan_s2v" and dialogue_text:
            clip_dur = float(os.environ.get("CLIP_DURATION_S", "10"))
            local_audio = out_path.parent / f".{out_path.stem}.s2v_input.mp3"
            print(f"  · S2V: generating conditioning audio (target ≤ {clip_dur:.1f}s)", flush=True)
            if _generate_hindi_audio(dialogue_text, local_audio, target_max_s=clip_dur):
                pregen_audio_path = local_audio
                audio_s = _audio_duration_s(local_audio)
                # Length: ceil(audio_s * fps) + 1, rounded up so (n-1) % 4 == 0
                fps = 24
                raw_len = math.ceil(audio_s * fps) + 1
                length = raw_len + ((4 - ((raw_len - 1) % 4)) % 4)
                length = max(81, length)  # min ~3.3s for stability
                print(f"  · S2V: audio={audio_s:.2f}s → length={length} frames "
                      f"(~{(length-1)/fps:.1f}s @ {fps}fps)", flush=True)
                uploaded_audio = _upload_file_to_comfyui(local_audio)
                for nid, node in patched.items():
                    if not isinstance(node, dict):
                        continue
                    if node.get("class_type") == "LoadAudio":
                        node["inputs"]["audio"] = uploaded_audio
                    elif node.get("class_type") == "WanSoundImageToVideo":
                        node["inputs"]["length"] = length
                print(f"  · S2V: audio uploaded as {uploaded_audio}", flush=True)
            else:
                print("  · S2V: audio generation failed — proceeding without lip-sync conditioning",
                      flush=True)
        # VBVR / I2V injection logic is LTX-specific; skip for Wan.
    else:
        # LTX path: optional LoadImage + LTXVImgToVideo injection for I2V
        if SKIP_IMAGES:
            print("  · text-only mode (SKIP_IMAGES): using prompt-only T2V", flush=True)
        elif COMFYUI_I2V and image_path and image_path.exists():
            print(f"  · uploading {image_path.name} → ComfyUI for I2V conditioning", flush=True)
            uploaded = _upload_image_to_comfyui(image_path)
            _inject_i2v(patched, uploaded)
            print(f"  · i2v: first frame guided by {uploaded} (strength={COMFYUI_I2V_STRENGTH})",
                  flush=True)
        elif COMFYUI_I2V and not image_path:
            print("  · i2v: no img_NN_* found in run dir — falling back to T2V", flush=True)
        # VBVR LoRA — LTX-only feature
        if COMFYUI_VBVR_LORA and COMFYUI_VBVR_STRENGTH > 0:
            _inject_vbvr_lora(patched, COMFYUI_VBVR_LORA, COMFYUI_VBVR_STRENGTH)
    client_id = str(uuid.uuid4())
    print(f"  · submitting to ComfyUI ({COMFYUI_URL})", flush=True)
    pid = _queue_prompt(patched, client_id)
    print(f"  · prompt_id={pid}, waiting (up to {GEN_TIMEOUT_S}s)", flush=True)
    entry = _wait_for_completion(pid)
    filename, subfolder = _extract_output_ref(entry)
    print(f"  · downloading {filename} from ComfyUI → {out_path.name}", flush=True)
    _fetch_output(filename, subfolder, out_path)
    print(f"  · saved ({out_path.stat().st_size // 1024} KB)", flush=True)
    # Post-gen: if the workflow produced silent video (Wan TI2V/S2V), overlay
    # audio. For S2V we reuse the exact wav that conditioned the lip-sync so
    # mouth and waveform stay aligned; for TI2V we generate fresh TTS sized to
    # the video duration. LTX bakes its own audio so this becomes a no-op.
    if dialogue_text:
        try:
            _overlay_audio_on_silent_video(out_path, dialogue_text,
                                           pregen_audio=pregen_audio_path)
            if pregen_audio_path and pregen_audio_path.exists():
                pregen_audio_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"  · audio overlay failed (leaving silent): {e}", flush=True)
    return out_path


def generate_all(scripts_json: Path, out_dir: Path, headless: bool = False,
                 only: list[int] | None = None, parallel: bool = False) -> list[Path]:
    """Match the signature of generate_videos.generate_all.

    `headless` and `parallel` are accepted for API compatibility but currently
    unused — ComfyUI handles its own GPU scheduling and we submit one prompt
    at a time (concurrent prompts would just queue serially on the GPU anyway).
    """
    _ = headless, parallel  # explicitly mark unused (kept for signature compatibility)

    payload = json.loads(scripts_json.read_text())
    scripts = payload["scripts"]
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"comfyui: server={COMFYUI_URL}  workflow={COMFYUI_WORKFLOW}", flush=True)
    # Health check before doing any heavy lifting
    try:
        sys_stats = _http_get("/system_stats", timeout=8)
        ver = sys_stats.get("system", {}).get("comfyui_version", "?")
        print(f"comfyui: connected (version {ver})", flush=True)
    except Exception as e:
        raise RuntimeError(
            f"can't reach ComfyUI at {COMFYUI_URL} — check it's running and "
            f"COMFYUI_URL env var. ({e})"
        )

    workflow_template = _load_workflow_api_format(COMFYUI_WORKFLOW)

    pending = _collect_pending(scripts, out_dir, only)
    outputs: list[Path] = []
    for idx, s, out in pending:
        print(f"[{idx}/5] {s['object']}", flush=True)
        img = _find_image_for_slot(out_dir, idx)
        try:
            generate_one(workflow_template, s, idx, out, image_path=img)
            outputs.append(out)
        except Exception as e:
            print(f"  [{idx}/5] ✗ {e}", flush=True)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive ComfyUI to render LTX-2.3 videos")
    parser.add_argument("run_dir", type=Path, help="Per-run output dir (must contain scripts.json)")
    parser.add_argument("--headless", action="store_true",
                        help="Accepted for compatibility with the Grok step (ignored)")
    parser.add_argument("--only", type=int, nargs="+",
                        help="Only generate these indices (1-5)")
    parser.add_argument("--parallel", action="store_true",
                        help="Accepted for compatibility with the Grok step (ignored)")
    args = parser.parse_args()

    scripts_json = args.run_dir / "scripts.json"
    if not scripts_json.exists():
        sys.stderr.write(f"Missing {scripts_json}\n")
        return 1

    outputs = generate_all(scripts_json, args.run_dir, headless=args.headless,
                           only=args.only, parallel=args.parallel)
    print(f"\nGenerated {len(outputs)} videos in {args.run_dir}", file=sys.stderr)
    for p in outputs:
        print(p)
    return 0 if outputs else 2


if __name__ == "__main__":
    sys.exit(main())
