import streamlit as st
import requests
import re
import io
import zipfile
import base64
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from pydub import AudioSegment
from pydub.silence import split_on_silence

# =============================
# LOGIN SYSTEM
# =============================

USERS = {
    "Tejas": "Vobble123",
    "Suryansh": "Vobble123",
    "Rohil": "Vobble123",
    "Saidutt": "Vobble123",
    "Danny": "Vobble123",
    "Ryan": "Vobble123",
    "Nischay": "Vobble123",
    "Yatharth": "Vobble123",
}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🎙 Vobble Listen engine Audio Team Login")

    username = st.text_input("Name")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username in USERS and USERS[username] == password:
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("Invalid credentials")

    st.stop()

# =============================
# CONFIG
# =============================

# ElevenLabs
API_KEY = st.secrets["API_KEY"]
MODEL_ID = "eleven_v3"

# Hume (add to secrets: HUME_API_KEY="...")
HUME_API_KEY = st.secrets.get("HUME_API_KEY", "")

RETRIES = 3
TIMEOUT_SEC = 30

CROSSFADE_MS = 0
GAP_SAME_SPEAKER_MS = 100
GAP_SPEAKER_CHANGE_MS = 100

CLIP_FADE_IN_MS = 20
CLIP_FADE_OUT_MS = 40
CLIP_TAIL_PAD_MS = 60

# =============================
# VOICE TYPE PROFILES
# =============================

VOICE_TYPE_PROFILES = {
    "adult_male": {"stability": 0.0, "similarity_boost": 0.88, "style": 1.0, "use_speaker_boost": True},
    "adult_female": {"stability": 0.50, "similarity_boost": 0.90, "style": 0.80, "use_speaker_boost": True},
    "male_kid": {"stability": 0.50, "similarity_boost": 0.80, "style": 0.90, "use_speaker_boost": True},
    "female_kid": {"stability": 0.50, "similarity_boost": 0.78, "style": 0.95, "use_speaker_boost": False},
}

# =============================
# UTILITIES
# =============================

def normalize_name(name: str) -> str:
    return name.strip().lower()

def safe_filename(name: str) -> str:
    return re.sub(r"[^a-z0-9_\-]+", "_", name.lower()).strip("_")

def is_sfx_or_music_line(line: str) -> bool:
    l = line.strip().lower()
    return l.startswith("sfx:") or l.startswith("music:")

def parse_script_blocks(script_text: str) -> List[Tuple[str, str]]:
    """
    Supports BOTH:
    1) single-line: name: dialogue
    2) block format:
       name:
       [tag...]
       dialogue line 1
       dialogue line 2
       (blank or next name:)
    Returns list of tuples: (speaker, dialogue_text)
    """
    lines = script_text.splitlines()
    items: List[Tuple[str, str]] = []

    current_speaker = None
    current_dialogue_lines: List[str] = []

    def flush():
        nonlocal current_speaker, current_dialogue_lines
        if current_speaker and current_dialogue_lines:
            dialogue = " ".join([x.strip() for x in current_dialogue_lines if x.strip()])
            if dialogue.strip():
                items.append((current_speaker, dialogue.strip()))
        current_dialogue_lines = []

    speaker_line_re = re.compile(r"^\s*([^:]{1,60})\s*:\s*(.*)$")

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            flush()
            continue

        if is_sfx_or_music_line(stripped):
            continue

        # ignore pure bracket performance direction lines like [warm, loud]
        if stripped.startswith("[") and stripped.endswith("]"):
            continue

        m = speaker_line_re.match(line)
        if m:
            speaker = normalize_name(m.group(1))
            after = (m.group(2) or "").strip()

            flush()
            current_speaker = speaker

            if after:
                current_dialogue_lines.append(after)
            continue

        if current_speaker:
            current_dialogue_lines.append(stripped)

    flush()
    return items

def detect_characters_from_blocks(items: List[Tuple[str, str]]) -> List[str]:
    return sorted(list({sp for sp, _ in items}))

# Keep your existing cadence stabilizer (unchanged)
ALLOWED_PAUSE_TAGS = {"[pause]", "[short pause]", "[long pause]"}

def strip_unknown_brackets(s: str) -> str:
    def repl(m):
        tag = m.group(0).strip().lower()
        return m.group(0) if tag in ALLOWED_PAUSE_TAGS else ""
    return re.sub(r"\[[^\]]+\]", repl, s).strip()

def ensure_line_tail(text: str) -> str:
    t = strip_unknown_brackets(text.strip())
    if not t:
        return t
    if not re.search(r"(\[short pause\]|\[pause\]|\[long pause\])\s*$", t):
        if not t.endswith((".", "!", "?", ",")):
            t += "."
        t += " [short pause]"
    return t

# =============================
# AUDIO GENERATION (ElevenLabs)
# =============================

def generate_audio_eleven(text: str, voice_id: str, voice_settings: dict) -> Optional[AudioSegment]:
    t = ensure_line_tail(text)
    if not t:
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    data = {"text": t, "model_id": MODEL_ID, "voice_settings": voice_settings}

    response = None
    for _ in range(RETRIES):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=TIMEOUT_SEC)
            if response.status_code == 200 and response.content:
                break
        except requests.exceptions.RequestException:
            continue
    else:
        return None

    if response.status_code != 200:
        st.error(f"ElevenLabs API Error {response.status_code}: {response.text}")
        return None

    audio = AudioSegment.from_file(io.BytesIO(response.content), format="mp3")
    audio = audio.fade_in(CLIP_FADE_IN_MS).fade_out(CLIP_FADE_OUT_MS)
    audio += AudioSegment.silent(duration=CLIP_TAIL_PAD_MS)
    return audio

# =============================
# AUDIO GENERATION (Hume)
# =============================

def infer_quick_emotion_hint(text: str) -> str:
    t = text.strip()
    if t.count("!") >= 2:
        return "loud, excited"
    if "!" in t:
        return "excited"
    if t.endswith("?"):
        return "curious, questioning"
    return ""

def build_hume_description(base_desc: str, line_text: str, auto_hints: bool) -> str:
    base = (base_desc or "").strip()
    if not auto_hints:
        return base if base else "Expressive delivery, clear articulation."
    hint = infer_quick_emotion_hint(line_text)
    if hint:
        if base:
            return f"{base} Emotion hint: {hint}."
        return f"Expressive delivery. Emotion hint: {hint}."
    return base if base else "Expressive delivery, clear articulation."

def generate_audio_hume(text: str, voice_ref: dict, description: str) -> Optional[AudioSegment]:
    """
    Hume TTS:
      POST https://api.hume.ai/v0/tts
    """
    if not HUME_API_KEY:
        st.error("Missing HUME_API_KEY in Streamlit secrets.")
        return None

    url = "https://api.hume.ai/v0/tts"
    headers = {"X-Hume-Api-Key": HUME_API_KEY, "Content-Type": "application/json"}

    payload = {
        "utterances": [
            {"text": text, "description": description, "voice": voice_ref}
        ],
        "format": {"type": "mp3"},
        "num_generations": 1,
        "split_utterances": False,
        "strip_headers": True
    }

    response = None
    for _ in range(RETRIES):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SEC)
            if response.status_code == 200:
                break
        except requests.exceptions.RequestException:
            continue
    else:
        return None

    if response.status_code != 200:
        st.error(f"Hume API Error {response.status_code}: {response.text}")
        return None

    data = response.json()
    b64_audio = data["generations"][0]["audio"]
    audio_bytes = base64.b64decode(b64_audio)

    audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
    audio = audio.fade_in(CLIP_FADE_IN_MS).fade_out(CLIP_FADE_OUT_MS)
    audio += AudioSegment.silent(duration=CLIP_TAIL_PAD_MS)
    return audio

# =============================
# RECORDED FILE TAKES
# =============================

def split_into_takes(audio: AudioSegment, min_silence_len=300, silence_thresh_db=-38, keep_silence=100) -> List[AudioSegment]:
    chunks = split_on_silence(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh_db,
        keep_silence=keep_silence
    )
    takes = []
    for c in chunks:
        if len(c) < 60:
            continue
        takes.append(c.fade_in(5).fade_out(10))
    return takes

def parse_take_sequence(seq: str) -> List[int]:
    seq = seq.strip()
    if not seq:
        return []
    out = []
    for part in seq.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out

# =============================
# UI
# =============================

st.title("🎙 Vobble Audio Studio")

uploaded_file = st.file_uploader("Upload Script (.txt)", type=["txt"])

if uploaded_file:
    script_text = uploaded_file.read().decode("utf-8")

    parsed_items = parse_script_blocks(script_text)
    characters = detect_characters_from_blocks(parsed_items)

    if not parsed_items or not characters:
        st.warning("No dialogue detected. Use either 'name: dialogue' OR block format 'name:' then dialogue lines.")
        st.stop()

    st.subheader("🎭 Character Setup (Choose provider)")

    @dataclass
    class CharConfig:
        provider: str  # "eleven" | "hume" | "file"
        # eleven
        eleven_voice_id: str = ""
        eleven_profile: dict = None
        # hume
        hume_voice_mode: str = "id"     # "id" | "name"
        hume_voice_id: str = ""
        hume_voice_name: str = ""
        hume_provider: str = "HUME_AI"
        hume_base_desc: str = ""
        hume_auto_hints: bool = True
        # file
        file_takes: List[AudioSegment] = None
        take_sequence: List[int] = None

    char_cfgs: Dict[str, CharConfig] = {}

    for character in characters:
        st.markdown(f"### {character}")

        provider_ui = st.selectbox(
            f"Voice source for {character}",
            ["ElevenLabs (AI)", "Hume (AI)", "Recorded File (takes)"],
            key=f"{character}_provider"
        )

        if provider_ui.startswith("ElevenLabs"):
            voice_id = st.text_input(f"ElevenLabs Voice ID for {character}", key=f"{character}_voice")
            voice_type = st.selectbox(
                f"Voice Type for {character}",
                ["adult_male", "adult_female", "male_kid", "female_kid"],
                key=f"{character}_type"
            )
            char_cfgs[character] = CharConfig(
                provider="eleven",
                eleven_voice_id=voice_id.strip(),
                eleven_profile=VOICE_TYPE_PROFILES[voice_type],
            )

        elif provider_ui.startswith("Hume"):
            st.caption("Hume: script text stays plain; performance direction goes into the 'description' field.")
            mode = st.selectbox("Hume voice reference", ["id", "name"], key=f"{character}_h_mode")

            if mode == "id":
                h_voice_id = st.text_input("Hume voice id", key=f"{character}_h_id")
                h_voice_name = ""
                h_provider = "HUME_AI"
            else:
                h_voice_id = ""
                h_voice_name = st.text_input("Hume voice name", key=f"{character}_h_name")
                h_provider = st.selectbox("Hume provider", ["HUME_AI", "CUSTOM_VOICE"], key=f"{character}_h_provider")

            base_desc = st.text_area(
                "Base acting description (personality/tone/pacing)",
                value="Expressive, natural delivery. Clear articulation. Strong comedic timing if relevant.",
                key=f"{character}_h_desc",
                height=90
            )
            auto_hints = st.checkbox(
                "Auto emotion hints from punctuation (!, ?)",
                value=True,
                key=f"{character}_h_hints"
            )

            char_cfgs[character] = CharConfig(
                provider="hume",
                hume_voice_mode=mode,
                hume_voice_id=h_voice_id.strip(),
                hume_voice_name=h_voice_name.strip(),
                hume_provider=h_provider,
                hume_base_desc=base_desc.strip(),
                hume_auto_hints=auto_hints,
            )

        else:  # Recorded File
            up = st.file_uploader(f"Upload recorded audio for {character} (wav/mp3)", type=["wav", "mp3"], key=f"{character}_file")
            seq = st.text_input(
                "Take sequence (e.g., 1,3,2,1,2) — one number per line, loops if shorter",
                key=f"{character}_seq"
            )

            min_sil = st.slider("Min silence to split takes (ms)", 150, 900, 300, key=f"{character}_mins")
            sil_thresh = st.slider("Silence threshold (dBFS)", -60, -15, -38, key=f"{character}_sth")
            keep_sil = st.slider("Keep silence around takes (ms)", 0, 300, 100, key=f"{character}_keeps")

            takes = None
            if up is not None:
                b = up.read()
                fmt = "wav" if up.name.lower().endswith(".wav") else "mp3"
                audio = AudioSegment.from_file(io.BytesIO(b), format=fmt)
                takes = split_into_takes(audio, min_silence_len=min_sil, silence_thresh_db=sil_thresh, keep_silence=keep_sil)
                st.info(f"Detected takes: {len(takes)}")

            char_cfgs[character] = CharConfig(
                provider="file",
                file_takes=takes,
                take_sequence=parse_take_sequence(seq),
            )

    if st.button("🎬 Generate Episode (Full + Stems ZIP)"):

        # Validate
        for ch in characters:
            cfg = char_cfgs.get(ch)
            if cfg is None:
                st.error(f"Missing config for {ch}")
                st.stop()

            if cfg.provider == "eleven":
                if not cfg.eleven_voice_id:
                    st.error(f"Please enter ElevenLabs Voice ID for {ch}")
                    st.stop()

            if cfg.provider == "hume":
                if not HUME_API_KEY:
                    st.error("HUME_API_KEY missing in secrets.")
                    st.stop()
                if cfg.hume_voice_mode == "id" and not cfg.hume_voice_id:
                    st.error(f"Please enter Hume voice id for {ch}")
                    st.stop()
                if cfg.hume_voice_mode == "name" and not cfg.hume_voice_name:
                    st.error(f"Please enter Hume voice name for {ch}")
                    st.stop()

            if cfg.provider == "file":
                if not cfg.file_takes:
                    st.error(f"Upload recorded audio file (with takes) for {ch}")
                    st.stop()
                if not cfg.take_sequence:
                    st.error(f"Provide take sequence for {ch} (e.g., 1,3,2,1,2)")
                    st.stop()

        # Build BOTH: full mix + stems
        final_audio = AudioSegment.empty()
        character_tracks = {ch: AudioSegment.silent(duration=0) for ch in characters}

        timeline_position = 0
        last_speaker = None

        # recorded line counters per character
        file_line_index = {ch: 0 for ch in characters}

        progress = st.progress(0)
        total_lines = len(parsed_items)
        processed = 0

        for speaker, dialogue in parsed_items:
            processed += 1
            progress.progress(processed / total_lines)

            cfg = char_cfgs.get(speaker)
            if cfg is None:
                continue

            audio = None

            if cfg.provider == "eleven":
                audio = generate_audio_eleven(dialogue, cfg.eleven_voice_id, cfg.eleven_profile)

            elif cfg.provider == "hume":
                # voice reference object
                if cfg.hume_voice_mode == "id":
                    voice_ref = {"id": cfg.hume_voice_id}
                else:
                    voice_ref = {"name": cfg.hume_voice_name, "provider": cfg.hume_provider}

                desc = build_hume_description(cfg.hume_base_desc, dialogue, cfg.hume_auto_hints)
                audio = generate_audio_hume(dialogue, voice_ref, desc)

            else:  # recorded file
                takes = cfg.file_takes or []
                seq = cfg.take_sequence or []
                idx = file_line_index[speaker]
                file_line_index[speaker] += 1

                take_num = seq[idx % len(seq)]  # loop
                take_idx = max(0, take_num - 1)
                if take_idx >= len(takes):
                    take_idx = len(takes) - 1
                audio = takes[take_idx]

            if not audio:
                continue

            # GAP (consistent)
            gap = 0
            if last_speaker is not None:
                gap = GAP_SAME_SPEAKER_MS if last_speaker == speaker else GAP_SPEAKER_CHANGE_MS

            if gap > 0:
                final_audio += AudioSegment.silent(duration=gap)
                for ch in character_tracks:
                    character_tracks[ch] += AudioSegment.silent(duration=gap)
                timeline_position += gap

            # FULL MIX
            if len(final_audio) == 0:
                final_audio = audio
            else:
                final_audio = final_audio.append(audio, crossfade=CROSSFADE_MS)

            # STEMS
            duration = len(audio)

            for ch in character_tracks:
                if len(character_tracks[ch]) < timeline_position:
                    character_tracks[ch] += AudioSegment.silent(duration=timeline_position - len(character_tracks[ch]))

            character_tracks[speaker] += audio
            for ch in character_tracks:
                if ch != speaker:
                    character_tracks[ch] += AudioSegment.silent(duration=duration)

            timeline_position += duration
            last_speaker = speaker

        if len(final_audio) == 0:
            st.error("No audio was generated. Check: Voice IDs valid + script has dialogue under each speaker.")
            st.stop()

        # ZIP: full mix + stems
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            full_wav = io.BytesIO()
            final_audio.export(full_wav, format="wav", parameters=["-acodec", "pcm_s16le"])
            full_wav.seek(0)
            zf.writestr("vobble_episode_full.wav", full_wav.read())

            for ch, track in character_tracks.items():
                if len(track) < len(final_audio):
                    track += AudioSegment.silent(duration=len(final_audio) - len(track))
                elif len(track) > len(final_audio):
                    track = track[:len(final_audio)]

                stem_wav = io.BytesIO()
                track.export(stem_wav, format="wav", parameters=["-acodec", "pcm_s16le"])
                stem_wav.seek(0)
                zf.writestr(f"stems/{safe_filename(ch)}_stem.wav", stem_wav.read())

        zip_buffer.seek(0)

        st.success("✅ Episode + stems generated!")
        st.download_button(
            label="⬇ download episode + stems (zip)",
            data=zip_buffer,
            file_name="vobble_episode_and_stems.zip",
            mime="application/zip"
        )
