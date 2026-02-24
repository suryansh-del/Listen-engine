import streamlit as st
import requests
import re
import io
import wave
from datetime import datetime

# ---------------- LOGIN SYSTEM ---------------- #

USERS = {
    "Tejas": "Vobble123",
    "Suryansh": "Vobble123"
}

def login():
    st.title("🎙️ Listen Engine Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username in USERS and USERS[username] == password:
            st.session_state.logged_in = True
            st.session_state.username = username
        else:
            st.error("Invalid credentials")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    login()
    st.stop()

# ---------------- MAIN APP ---------------- #

st.title("🎧 Listen Engine – Script to Audio")

st.success(f"Logged in as {st.session_state.username}")

# -------- Ask for ElevenLabs API Key -------- #

if "api_key" not in st.session_state:
    st.session_state.api_key = ""

st.session_state.api_key = st.text_input(
    "Enter ElevenLabs API Key",
    type="password",
    value=st.session_state.api_key
)

API_KEY = st.session_state.api_key.strip()

# -------- Upload Script -------- #

uploaded_file = st.file_uploader("Upload Script (.txt)", type=["txt"])

def detect_characters(script_text):
    pattern = r"^([A-Za-z0-9 _-]+):"
    characters = set(re.findall(pattern, script_text, re.MULTILINE))
    return sorted(list(characters))

def parse_script(script_text):
    lines = script_text.split("\n")
    parsed = []

    for line in lines:
        match = re.match(r"^([A-Za-z0-9 _-]+):(.*)", line)
        if match:
            character = match.group(1).strip()
            dialogue = match.group(2).strip()
            parsed.append((character, dialogue))

    return parsed

def generate_audio(text, voice_id):

    if not API_KEY:
        st.error("Please enter your ElevenLabs API key.")
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=wav_44100"

    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2"
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload
    )

    if response.status_code != 200:
        st.error(f"API Error {response.status_code}: {response.text}")
        return None

    return response.content

def combine_wav_files(wav_files):

    output_buffer = io.BytesIO()

    with wave.open(output_buffer, 'wb') as output_wav:
        first_file = wave.open(io.BytesIO(wav_files[0]), 'rb')

        output_wav.setnchannels(first_file.getnchannels())
        output_wav.setsampwidth(first_file.getsampwidth())
        output_wav.setframerate(first_file.getframerate())

        output_wav.writeframes(first_file.readframes(first_file.getnframes()))
        first_file.close()

        for wav_data in wav_files[1:]:
            wav_file = wave.open(io.BytesIO(wav_data), 'rb')
            output_wav.writeframes(wav_file.readframes(wav_file.getnframes()))
            wav_file.close()

    output_buffer.seek(0)
    return output_buffer

# -------- If Script Uploaded -------- #

if uploaded_file:

    script_text = uploaded_file.read().decode("utf-8")
    characters = detect_characters(script_text)

    st.subheader("Detected Characters")

    character_settings = {}

    for char in characters:
        st.markdown(f"### {char}")

        gender = st.selectbox(
            f"Select Gender for {char}",
            ["Adult Male", "Adult Female", "Male Kid", "Female Kid"],
            key=f"{char}_gender"
        )

        voice_id = st.text_input(
            f"Enter ElevenLabs Voice ID for {char}",
            key=f"{char}_voice"
        )

        character_settings[char] = {
            "gender": gender,
            "voice_id": voice_id
        }

    if st.button("Generate Episode"):

        parsed_lines = parse_script(script_text)

        wav_segments = []

        for character, dialogue in parsed_lines:

            voice_id = character_settings[character]["voice_id"]

            audio_data = generate_audio(dialogue, voice_id)

            if audio_data:
                wav_segments.append(audio_data)

        if wav_segments:
            combined_audio = combine_wav_files(wav_segments)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"episode_{timestamp}.wav"

            st.download_button(
                label="Download Episode",
                data=combined_audio,
                file_name=filename,
                mime="audio/wav"
            )

            st.success("Episode generated successfully 🎉")
