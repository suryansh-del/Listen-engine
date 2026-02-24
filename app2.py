import streamlit as st
import requests
import re
import io
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

def normalize_name(name):
    return name.strip().lower()

def detect_characters(script_text):
    characters = set()
    lines = script_text.split("\n")

    for line in lines:
        line = line.strip()
        if ":" in line:
            speaker = line.split(":", 1)[0].strip()
            if speaker:
                characters.add(normalize_name(speaker))

    return sorted(list(characters))

ALLOWED_PAUSE_TAGS = {"[pause]", "[short pause]", "[long pause]"}

def strip_unknown_brackets(s):
    def repl(m):
        tag = m.group(0).strip().lower()
        return m.group(0) if tag in ALLOWED_PAUSE_TAGS else ""
    return re.sub(r"\[[^\]]+\]", repl, s).strip()

def ensure_line_tail(text):
    t = strip_unknown_brackets(text.strip())
    if not t:
        return t

    if not re.search(r"(\[short pause\]|\[pause\]|\[long pause\])\s*$", t):
        if not t.endswith((".", "!", "?", ",")):
            t += "."
        t += " [short pause]"

    return t

# =============================
# AUDIO GENERATION
# =============================

def generate_audio(text, voice_id, voice_settings):
    t = ensure_line_tail(text)
    if not t:
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_44100"

    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/wav"
    }

    data = {
        "text": t,
        "model_id": MODEL_ID,
        "voice_settings": voice_settings
    }

    for attempt in range(RETRIES):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=TIMEOUT_SEC)
            break
        except requests.exceptions.RequestException:
            continue
    else:
        return None

    if response.status_code != 200:
        st.error(f"API Error {response.status_code}: {response.text}")
        return None

    audio = AudioSegment(
        data=response.content,
        sample_width=2,
        frame_rate=44100,
        channels=1
    )

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

        final_audio = AudioSegment.empty()
        prev_speaker = None

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

            if ":" in line:
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

                if audio:
                    if len(final_audio) == 0:
                        final_audio = audio
                    else:
                        final_audio = final_audio.append(audio, crossfade=CROSSFADE_MS)

                    if prev_speaker == speaker:
                        final_audio += AudioSegment.silent(duration=GAP_SAME_SPEAKER_MS)
                    else:
                        final_audio += AudioSegment.silent(duration=GAP_SPEAKER_CHANGE_MS)

                    prev_speaker = speaker

        wav_io = io.BytesIO()
        final_audio.export(
            wav_io,
            format="wav",
            parameters=["-acodec", "pcm_s16le"]
        )

        wav_io.seek(0)

        st.success("✅ Episode Generated Successfully!")

        st.download_button(
            label="⬇ Download Episode",
            data=wav_io,
            file_name="vobble_episode.wav",
            mime="audio/wav"

        )
