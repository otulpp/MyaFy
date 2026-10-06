from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
import shutil
import re
import subprocess
import json
import os
from datetime import datetime

from mutagen import File as MutagenFile


app = Flask(__name__)


# ============================================================
# tha config stuff (BOOOOORINGGGGG)

BASE_DIR = Path("/home/mya/MyaFy")

UPLOAD_FOLDER = Path("/mnt/mybook/MyaFy-Uploads")

MUSIC_FOLDER = Path("/mnt/mybook/Music")

# Upload password is provided through the MYAFY_UPLOAD_PASSWORD environment variable.
UPLOAD_PASSWORD = os.environ["MYAFY_UPLOAD_PASSWORD"]

JELLYFIN_URL = "http://127.0.0.1:8096"

HISTORY_FILE = BASE_DIR / "upload_history.json"
MAX_HISTORY = 50

ALLOWED_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
}


#yeah these files exist

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
MUSIC_FOLDER.mkdir(parents=True, exist_ok=True)


# my file slaves (jk but they help organize)

def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def clean_name(value, fallback="Unknown"):
    """
    Makes metadata safe to use as a Linux folder/file name.
    """

    if not value:
        return fallback

    value = str(value).strip()

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        value
    )

    value = value.rstrip(". ")

    if not value or value in (".", ".."):
        return fallback

    return value


def unique_path(path):
    """
    Prevent overwriting an existing file.
    """

    if not path.exists():
        return path

    counter = 2

    while True:

        new_path = path.with_name(
            f"{path.stem} ({counter}){path.suffix}"
        )

        if not new_path.exists():
            return new_path

        counter += 1


# #########################################

def first_tag_value(tags, names):
    """
    Look for the first available metadata tag.

    Works with common MP3, FLAC, M4A, OGG, and other
    formats supported by Mutagen.
    """

    if not tags:
        return None

    for name in names:

        try:

            value = tags.get(name)

            if value is None:
                continue

            # Mutagen EasyID3 / Easy tags often return lists.
            if isinstance(value, list):

                if not value:
                    continue

                value = value[0]

            value = str(value).strip()

            if value:
                return value

        except Exception:
            continue

    return None


def read_audio_metadata(file_path):
    """
    Read Artist and Album from embedded audio metadata.
    """

    artist = None
    album = None

    try:

        audio = MutagenFile(
            str(file_path),
            easy=True
        )

        if audio is None:

            print(
                f"couldnt read metadata: "
                f"{file_path.name}"
            )

            return {
                "artist": None,
                "album": None
            }


        tags = audio.tags


        # Artist
        artist = first_tag_value(
            tags,
            [
                "artist",
                "albumartist",
            ]
        )


        # Album
        album = first_tag_value(
            tags,
            [
                "album",
            ]
        )


    except Exception as error:

        print(
            f"Metadata error for "
            f"{file_path.name}: {error}"
        )


    return {
        "artist": artist,
        "album": album
    }


# ============================================================
# JELLYFIN

def scan_jellyfin():

    print("Requesting Jellyfin library scan...")

    try:

        result = subprocess.run(
            [
                "curl",
                "-s",
                "-X",
                "POST",
                f"{JELLYFIN_URL}/Library/Refresh",
            ],
            capture_output=True,
            text=True,
            timeout=20
        )

        print(
            "Jellyfin scan request completed "
            f"with exit code {result.returncode}"
        )

        return result.returncode == 0

    except Exception as error:

        print(
            f"Jellyfin scan request failed: {error}"
        )

        return False


# ============================================================
# ORGANIZE MUSIC

def organize_music(file_path):
    """
    Read Artist and Album metadata and move the file to:

        Music/
            Artist/
                Album/
                    track.ext
    """

    metadata = read_audio_metadata(file_path)

    artist = clean_name(
        metadata["artist"],
        "Unknown Artist"
    )

    album = clean_name(
        metadata["album"],
        "Unknown Album"
    )


    
    # /mnt/mybook/Music/Artist/Album/

    album_folder = (
        MUSIC_FOLDER
        / artist
        / album
    )

    album_folder.mkdir(
        parents=True,
        exist_ok=True
    )


    # keep the original uploaded filename
    destination = album_folder / file_path.name

    destination = unique_path(destination)


    shutil.move(
        str(file_path),
        str(destination)
    )


    print(
        f"Organized: "
        f"{artist} / {album} / {destination.name}"
    )


    return {
        "artist": artist,
        "album": album,
        "filename": destination.name,
        "path": str(destination)
    }


# ============================================================
# ROUTES

@app.route("/")
def index():

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


@app.route("/health")
def health():

    return jsonify({
        "status": "ok"
    })


def load_upload_history():
    if not HISTORY_FILE.exists():
        return []

    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:
        print(f"Failed to read upload history: {error}")

    return []


def record_upload_history(
    status,
    filename,
    artist=None,
    album=None,
    error=None
):
    history = load_upload_history()

    entry = {
        "time": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "status": status,
        "filename": filename
    }

    if artist:
        entry["artist"] = artist

    if album:
        entry["album"] = album

    if error:
        entry["error"] = error

    history.insert(0, entry)
    history = history[:MAX_HISTORY]

    temporary_history = HISTORY_FILE.with_suffix(
        ".json.tmp"
    )

    try:

        with temporary_history.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                history,
                file,
                indent=2
            )

        temporary_history.replace(
            HISTORY_FILE
        )

    except Exception as save_error:

        print(
            f"Failed to save upload history: "
            f"{save_error}"
        )


@app.route("/upload-history", methods=["GET"])
def upload_history():

    return jsonify({
        "history": load_upload_history()
    })


@app.route("/upload", methods=["POST"])
def upload():

    # dont hardcode your passwords kids!

    password = request.form.get(
        "password",
        ""
    )

    if password != UPLOAD_PASSWORD:

        record_upload_history(
            "failed",
            "Upload attempt",
            error="Incorrect password"
        )

        return jsonify({
            "error": "incorrect password."
        }), 403


    # get noob (files)

    uploaded_files = request.files.getlist(
        "file"
    )

    if not uploaded_files:

        record_upload_history(
            "failed",
            "Upload attempt",
            error="No files selected"
        )

        return jsonify({
            "error": "no files selected."
        }), 400


    results = []
    errors = []


    # now PUT THEM WHERE THEY BELONG

    for uploaded_file in uploaded_files:

        if not uploaded_file.filename:
            continue


        original_name = Path(
            uploaded_file.filename
        ).name


        # Check extension.
        if not allowed_file(original_name):

            errors.append(
                f"{original_name}: "
                f"file type not allowed"
            )

            record_upload_history(
                "failed",
                original_name,
                error="File type not allowed"
            )

            continue


        # if youre still reading this then you must reallllyyyyyy like me :3

        temporary_path = unique_path(
            UPLOAD_FOLDER / original_name
        )


        try:

            uploaded_file.save(
                str(temporary_path)
            )


            # ------------------------------------------------
            # READ METADATA + ORGANIZE

            result = organize_music(
                temporary_path
            )


            results.append(result)

            record_upload_history(
                "success",
                result["filename"],
                artist=result["artist"],
                album=result["album"]
            )


        except Exception as error:

            print(
                f"Failed to process "
                f"{original_name}: {error}"
            )


            # Remove failed temporary upload.
            if temporary_path.exists():

                try:
                    temporary_path.unlink()

                except Exception:
                    pass


            errors.append(
                f"{original_name}: upload failed"
            )

            record_upload_history(
                "failed",
                original_name,
                error="Upload failed"
            )


    # scan all my files please

    jellyfin_scanned = False

    if results:

        jellyfin_scanned = scan_jellyfin()


    # lmk tho 

    return jsonify({

        "message":
            f"{len(results)} file(s) uploaded "
            f"and organized successfully.",

        "uploaded":
            results,

        "errors":
            errors,

        "jellyfin_scanned":
            jellyfin_scanned

    })


# hehe i made it pretty

if __name__ == "__main__":

    print("⋆˖⁺‧₊☽◯☾₊‧⁺˖⋆")
    print("MyaFy BOOTINGGG")
    print(f"Music folder: {MUSIC_FOLDER}")
    print("⋆˖⁺‧₊☽◯☾₊‧⁺˖⋆")

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False
    )
  # if u see this that means youre stalking me 
    # and if u see something wrong with the code then 
    # come embarrass me on discord @otulpp 
    # that would really suck if this fucked up tho hopefully
    # i dont have to come back here





#yeah ok im back already
