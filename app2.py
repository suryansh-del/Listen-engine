import streamlit as st
import requests
import re
import io
import wave
import struct

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
# API KEY CHECK
# =============================

API_KEY = st.secrets.get("API_KEY")

if not API_KEY:
    st.error("❌ API_KEY not found in Streamlit Secrets.")
    st.stop()

MODEL_ID = "eleven_v3"

# =============================
# TIMING CONFIG
# =============================

GAP_SAME_SPEAKER_MS = 400
GAP_SPEAKER_CHANGE_MS = 800

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
    for line in script_text.split("\n"):
        if ":" in line:
            speaker = line.split(":", 1)[0].strip()
            if speaker:
                characters.add(normalize_name(speaker))
    return sorted(list(characters))

def generate_audio(text, voice_id, voice_settings):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=wav_44100"

    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json"
}
    }

    data = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": voice_settings
    }

    response = requests.post(url, json=data)

    if response.status_code != 200:
        st.error(f"API Error {response.status_code}: {response.text}")
        return None

    return response.content

def create_silence(duration_ms, params):
    frame_rate, sampwidth, channels = params
    num_frames = int(frame_rate * duration_ms / 1000)
    silence_frame = struct.pack("<h", 0)
    silence = silence_frame * channels * num_frames
    return silence

# =============================
# UI
# =============================

st.title("🎙 Vobble Audio Studio")

uploaded_file = st.file_uploader("Upload Script (.txt)", type=["txt"])

if uploaded_file:

    script_text = uploaded_file.read().decode("utf-8")
    characters = detect_characters(script_text)

    if not characters:
        st.warning("Use format: Name: dialogue")
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
            st.error("Assign Voice ID for all characters.")
            st.stop()

        lines = script_text.split("\n")

        wav_segments = []
        audio_params = None

        for raw in lines:
            line = raw.strip()
            if not line or ":" not in line:
                continue

            speaker_part, dialogue = line.split(":", 1)
            speaker = normalize_name(speaker_part)
            dialogue = dialogue.strip()

            if speaker not in voice_map:
                continue

            audio_bytes = generate_audio(
                dialogue,
                voice_map[speaker],
                voice_profiles[speaker]
            )

            if not audio_bytes:
                continue

            wav_file = wave.open(io.BytesIO(audio_bytes), "rb")

            if audio_params is None:
                audio_params = (
                    wav_file.getframerate(),
                    wav_file.getsampwidth(),
                    wav_file.getnchannels()
                )

            frames = wav_file.readframes(wav_file.getnframes())
            wav_segments.append((speaker, frames))

        if not wav_segments:
            st.error("No audio generated.")
            st.stop()

        output = io.BytesIO()

        with wave.open(output, "wb") as out:
            out.setnchannels(audio_params[2])
            out.setsampwidth(audio_params[1])
            out.setframerate(audio_params[0])

            for i, (speaker, frames) in enumerate(wav_segments):

                if i > 0:
                    prev_speaker = wav_segments[i - 1][0]

                    if prev_speaker == speaker:
                        silence = create_silence(GAP_SAME_SPEAKER_MS, audio_params)
                    else:
                        silence = create_silence(GAP_SPEAKER_CHANGE_MS, audio_params)

                    out.writeframes(silence)

                out.writeframes(frames)

        output.seek(0)

        st.success("✅ Episode Generated Successfully!")

        st.download_button(
            "⬇ Download Episode",
            data=output,
            file_name="vobble_episode.wav",
            mime="audio/wav"
        )

