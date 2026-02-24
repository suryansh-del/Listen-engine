import streamlit as st
import requests
import re
import tempfile
import subprocess
import os
import io
from datetime import datetime

# ==============================
# SESSION INIT
# ==============================

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "username" not in st.session_state:
    st.session_state.username = ""

if "api_key" not in st.session_state:
    st.session_state.api_key = ""

# ==============================
# LOGIN
# ==============================

USERS = {
    "Tejas": "Vobble123",
    "Suryansh": "Vobble123"
}

if not st.session_state.logged_in:

    st.title("🎙️ Listen Engine Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username in USERS and USERS[username] == password:
            st.session_state.logged_in = True
            st.session_state.username = username
            st.rerun()
        else:
            st.error("Invalid credentials")

    st.stop()

# ==============================
# MAIN UI
# ==============================

st.title("🎧 Listen Engine – Production Build")
st.success(f"Logged in as {st.session_state.username}")

# ==============================
# API KEY INPUT
# ==============================

st.session_state.api_key = st.text_input(
    "Enter ElevenLabs API Key",
    type="password",
    value=st.session_state.api_key
)

API_KEY = st.session_state.api_key.strip()

# ==============================
# GLOBAL VOICE SETTINGS
# ==============================

st.subheader("Global Voice Settings")

stability = st.selectbox("Stability", [0.0, 0.5, 1.0], index=1)
similarity_boost = st.slider("Similarity Boost", 0.0, 1.0, 0.75)
silence_gap = st.slider("Silence Between Dialogues (seconds)", 0.0, 2.0, 0.4)

# ==============================
# SCRIPT UPLOAD
# ==============================

uploaded_file = st.file_uploader("Upload Script (.txt)", type=["txt"])

# ==============================
# SCRIPT PARSING
# ==============================

def detect_characters(script_text):
    pattern = r"^([A-Za-z0-9 _-]+):"
    return sorted(set(re.findall(pattern, script_text, re.MULTILINE)))

def parse_script(script_text):
    lines = script_text.split("\n")
    parsed = []

    for line in lines:
        match = re.match(r"^([A-Za-z0-9 _-]+):(.*)", line)
        if match:
            character = match.group(1).strip()
            dialogue = match.group(2).strip()
            if dialogue:
                parsed.append((character, dialogue))

    return parsed

# ==============================
# ELEVENLABS AUDIO GEN
# ==============================

def generate_audio(text, voice_id):

    if not API_KEY:
        st.error("Please enter ElevenLabs API key.")
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=wav_44100"

    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost
        }
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        st.error(f"API Error {response.status_code}: {response.text}")
        return None

    return response.content

# ==============================
# FFMPEG CONCAT
# ==============================

def combine_with_ffmpeg(wav_segments):

    temp_files = []

    # Save segments
    for segment in wav_segments:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp.write(segment)
        temp.close()
        temp_files.append(temp.name)

    # Create concat list
    list_file = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix=".txt")
    for file in temp_files:
        list_file.write(f"file '{file}'\n")
    list_file.close()

    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name

    command = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file.name,
        "-c:a", "pcm_s16le",
        output_file
    ]

    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    with open(output_file, "rb") as f:
        final_audio = f.read()

    # Cleanup
    for file in temp_files:
        os.remove(file)
    os.remove(list_file.name)
    os.remove(output_file)

    return io.BytesIO(final_audio)

# ==============================
# CHARACTER SETUP
# ==============================

if uploaded_file:

    script_text = uploaded_file.read().decode("utf-8")
    characters = detect_characters(script_text)

    st.subheader("Detected Characters")

    character_settings = {}

    for char in characters:
        st.markdown(f"### {char}")

        voice_id = st.text_input(
            f"Voice ID for {char}",
            key=f"{char}_voice"
        )

        character_settings[char] = voice_id

    if st.button("Generate Episode"):

        parsed_lines = parse_script(script_text)
        wav_segments = []

        for character, dialogue in parsed_lines:

            voice_id = character_settings.get(character)

            if not voice_id:
                st.error(f"No voice ID set for {character}")
                st.stop()

            audio_data = generate_audio(dialogue, voice_id)

            if audio_data:
                wav_segments.append(audio_data)

                # Add silence gap
                if silence_gap > 0:
                    silence_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
                    subprocess.run([
                        "ffmpeg",
                        "-f", "lavfi",
                        "-i", f"anullsrc=r=44100:cl=mono",
                        "-t", str(silence_gap),
                        "-acodec", "pcm_s16le",
                        silence_file
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                    with open(silence_file, "rb") as f:
                        wav_segments.append(f.read())

                    os.remove(silence_file)

        if wav_segments:

            final_audio = combine_with_ffmpeg(wav_segments)

            filename = f"episode_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

            st.download_button(
                "Download Episode",
                data=final_audio,
                file_name=filename,
                mime="audio/wav"
            )

            st.success("🎉 Episode generated successfully!")
