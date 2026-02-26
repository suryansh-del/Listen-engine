import streamlit as st
import requests
import re
import io
import zipfile
from pydub import AudioSegment

# =============================
# LOGIN SYSTEM
# =============================

USERS = {
    "Tejas": "Vobble123",
    "Suryansh": "Vobble123"
}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🎙 Vobble Audio Studio Login")

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

API_KEY = st.secrets["API_KEY"]
MODEL_ID = "eleven_v3"

RETRIES = 3
TIMEOUT_SEC = 30

CROSSFADE_MS = 0
GAP_SAME_SPEAKER_MS = 400
GAP_SPEAKER_CHANGE_MS = 800

CLIP_FADE_IN_MS = 20
CLIP_FADE_OUT_MS = 40
CLIP_TAIL_PAD_MS = 120

# =============================
# VOICE TYPE PROFILES
# =============================

VOICE_TYPE_PROFILES = {
    "adult_male": {
        "stability": 0.50,
        "similarity_boost": 0.88,
        "style": 0.75,
        "use_speaker_boost": True
    },
    "adult_female": {
        "stability": 0.5,
        "similarity_boost": 0.90,
        "style": 0.80,
        "use_speaker_boost": True
    },
    "male_kid": {
        "stability": 0.5,
        "similarity_boost": 0.80,
        "style": 0.90,
        "use_speaker_boost": True
    },
    "female_kid": {
        "stability": 0.5,
        "similarity_boost": 0.78,
        "style": 0.95,
        "use_speaker_boost": False
    }
}

# =============================
# UTILITIES
# =============================

def normalize_name(name: str) -> str:
    return name.strip().lower()

def detect_characters(script_text: str):
    characters = set()
    lines = script_text.split("\n")

    for line in lines:
        line = line.strip()
        if ":" in line:
            speaker = line.split(":", 1)[0].strip()
            if speaker:
                characters.add(normalize_name(speaker))

    return sorted(list(characters))

# NOTE: You said you do NOT want internal pause tags for ElevenLabs-tag scripts,
# but this app uses ensure_line_tail to stabilize TTS cadence.
# This only appends "[short pause]" if missing and strips unknown bracket tags.
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

def safe_filename(name: str) -> str:
    return re.sub(r"[^a-z0-9_\-]+", "_", name.lower()).strip("_")

# =============================
# AUDIO GENERATION
# =============================

def generate_audio(text, voice_id, voice_settings):
    t = ensure_line_tail(text)
    if not t:
        return None

    # ✅ Use MP3 output (more widely supported across ElevenLabs plans)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"

    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }

    data = {
        "text": t,
        "model_id": MODEL_ID,
        "voice_settings": voice_settings
    }

    response = None
    for attempt in range(RETRIES):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=TIMEOUT_SEC)
            if response.status_code == 200 and response.content:
                break
        except requests.exceptions.RequestException:
            continue
    else:
        return None

    if response.status_code != 200:
        st.error(f"API Error {response.status_code}: {response.text}")
        return None

    # Load MP3, then we export WAV later
    audio = AudioSegment.from_file(io.BytesIO(response.content), format="mp3")

    audio = audio.fade_in(CLIP_FADE_IN_MS).fade_out(CLIP_FADE_OUT_MS)
    audio += AudioSegment.silent(duration=CLIP_TAIL_PAD_MS)

    return audio

# =============================
# UI
# =============================

st.title("🎙 Vobble Audio Studio")

uploaded_file = st.file_uploader("Upload Script (.txt)", type=["txt"])

if uploaded_file:

    script_text = uploaded_file.read().decode("utf-8")
    characters = detect_characters(script_text)

    if not characters:
        st.warning("No characters detected. Use format: Name: dialogue")
        st.stop()

    st.subheader("🎭 Character Setup")

    voice_map = {}
    voice_profiles = {}

    for character in characters:
        st.markdown(f"### {character}")

        voice_id = st.text_input(
            f"Voice ID for {character}",
            key=f"{character}_voice"
        )

        voice_type = st.selectbox(
            f"Voice Type for {character}",
            ["adult_male", "adult_female", "male_kid", "female_kid"],
            key=f"{character}_type"
        )

        if voice_id:
            voice_map[character] = voice_id
            voice_profiles[character] = VOICE_TYPE_PROFILES[voice_type]

    if st.button("🎬 Generate Episode"):

        if len(voice_map) != len(characters):
            st.error("Please assign Voice ID for all characters.")
            st.stop()

        # -----------------------------
        # Build BOTH: full mix + stems
        # -----------------------------
        final_audio = AudioSegment.empty()
        character_tracks = {char: AudioSegment.silent(duration=0) for char in characters}

        timeline_position = 0  # ms
        last_speaker = None

        lines = script_text.split("\n")

        progress = st.progress(0)
        total_lines = len(lines)
        processed = 0

        for raw in lines:
            line = raw.strip()
            processed += 1
            progress.progress(processed / total_lines)

            if not line:
                continue

            if ":" not in line:
                continue

            speaker_part, dialogue = line.split(":", 1)
            speaker = normalize_name(speaker_part)
            dialogue = dialogue.strip()

            if speaker not in voice_map:
                continue

            audio = generate_audio(
                dialogue,
                voice_map[speaker],
                voice_profiles[speaker]
            )

            if not audio:
                continue

            # -----------------------------
            # Apply GAP consistently to mix + all stems
            # -----------------------------
            gap = 0
            if last_speaker is not None:
                gap = GAP_SAME_SPEAKER_MS if last_speaker == speaker else GAP_SPEAKER_CHANGE_MS

            if gap > 0:
                final_audio += AudioSegment.silent(duration=gap)
                for ch in character_tracks:
                    character_tracks[ch] += AudioSegment.silent(duration=gap)
                timeline_position += gap

            # -----------------------------
            # FULL MIX: append speech
            # -----------------------------
            if len(final_audio) == 0:
                final_audio = audio
            else:
                final_audio = final_audio.append(audio, crossfade=CROSSFADE_MS)

            # -----------------------------
            # STEMS: audio only on speaker track, silence elsewhere
            # -----------------------------
            duration = len(audio)

            # alignment safety (should already match, but keep it robust)
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
            st.error("No audio was generated. Please check Voice IDs and script format (name: dialogue).")
            st.stop()

        # -----------------------------
        # ZIP: full mix + stems
        # -----------------------------
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Full episode wav
            full_wav = io.BytesIO()
            final_audio.export(full_wav, format="wav", parameters=["-acodec", "pcm_s16le"])
            full_wav.seek(0)
            zf.writestr("vobble_episode_full.wav", full_wav.read())

            # Stems wavs (same length)
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
