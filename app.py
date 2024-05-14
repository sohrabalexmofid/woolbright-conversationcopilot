from flask import Flask, request, jsonify, render_template, send_file
import replicate
import tempfile
import os
import boto3
import requests
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Retrieve the Replicate API token
replicate_api_token = os.getenv("REPLICATE_API_TOKEN")
if not replicate_api_token:
    raise ValueError("The REPLICATE_API_TOKEN environment variable is not set. Please check your .env file.")

# Set the Replicate API token in the environment for Replicate API
os.environ['REPLICATE_API_TOKEN'] = replicate_api_token

# AWS S3 credentials and bucket name
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("BUCKET_NAME")

# Check that all AWS credentials and the bucket name are provided
if not all([AWS_ACCESS_KEY, AWS_SECRET_ACCESS_KEY, BUCKET_NAME]):
    raise ValueError("One or more AWS credentials are missing. Please check your .env file.")

# Initialize the S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

# Initialize the Flask application using the correct `__name__` variable
app = Flask(__name__)
model = replicate
transcript_memory = "" # initialize transcript memory

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process-audio", methods=["POST"])
def process_audio_data():
    audio_data = request.files["audio"].read()

    try:
        # Create a temporary file to hold the audio data
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
            temp_audio.write(audio_data)
            temp_audio.flush()
            audio_key = os.path.basename(temp_audio.name)

            # Upload the audio file to S3
            s3_client.upload_file(temp_audio.name, BUCKET_NAME, audio_key)
            audio_url = f"https://{BUCKET_NAME}.s3.amazonaws.com/{audio_key}"
        
        # Verify if the URL is accessible
            response = requests.head(audio_url)
            if response.status_code != 200:
                raise ValueError(f"Audio file not accessible: {audio_url}")

        # Use Replicate to transcribe the audio file
        output = replicate.run(
            "vaibhavs10/incredibly-fast-whisper:3ab86df6c8f54c11309d4d1f930ac292bad43ace52d10c80d87eb258b3c9f79c",
            input={
                "task": "transcribe",
                "audio": audio_url,
                "language": "english",
                "timestamp": "chunk",
                "batch_size": 64,
                "diarise_audio": False
            }
        )
        global transcript_memory
        transcript_memory += "\n" +output["text"] # append to memory
        print(output)
        results = output["text"]

        return jsonify({"transcript": results})
    except Exception as e:
        print(f"Error running Replicate model: {e}")
        return jsonify({"error": str(e)}), 500
    

    # Route to generate suggestions based on the transcript
@app.route("/get-suggestion", methods=["POST"])
def get_suggestion():
    print("Getting Suggestion...")
    data = request.get_json()
    global transcript_memory
    transcript = data.get("transcript", "")
    prompt_text = data.get("prompt", "")

    prompt = f"Previous Conversation:\n{transcript_memory}\n\nNew Prompt:\n{data.get('prompt', '')}"

    suggestion = ""
    for event in replicate.stream(
        "mistralai/mistral-7b-instruct-v0.2",
        input={
            "prompt": prompt,
            "temperature": 0.6,
            "max_new_tokens": 512,
            "min_new_tokens": -1,
            "prompt_template": "<s>[INST] {prompt} [/INST] ",
            "top_k": 50,
            "top_p": 0.9,
            "debug": False,
            "repetition_penalty": 1.3,
        },
    ):
        suggestion += str(event)

    return jsonify({"suggestion": suggestion})
@app.route("/download-transcript", methods=["GET"])
def download_transcript():
    transcript_file_path = request.args.get("file_path")
    if not transcript_file_path or not os.path.exists(transcript_file_path):
        return jsonify({"error": "Transcript file not found"}), 404
    return send_file(transcript_file_path, as_attachment=True, download_name="transcript.txt")

if __name__ == "__main__":
    app.run(debug=True)