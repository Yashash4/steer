#!/bin/bash
# Local speech-to-text for the reel's audio check: whisper.cpp built from source inside code/.tools, and its English
# base model in code/.models. Both folders are gitignored. Pinned tag, commit and model hash; any mismatch stops.
set -euo pipefail
CODE="$(cd "$(dirname "$0")/.." && pwd)"
TAG=v1.9.1
COMMIT=f049fff95a089aa9969deb009cdd4892b3e74916
MODEL=ggml-base.en.bin
MODEL_SHA256=a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002
SRC="$CODE/.tools/whisper.cpp"

if [ ! -d "$SRC/.git" ]; then
  git clone --quiet --depth 1 --branch "$TAG" https://github.com/ggml-org/whisper.cpp "$SRC"
fi
[ "$(git -C "$SRC" rev-parse HEAD)" = "$COMMIT" ] || { echo "whisper.cpp is not at $TAG ($COMMIT)" >&2; exit 1; }
cmake -S "$SRC" -B "$SRC/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_SERVER=OFF > /dev/null
cmake --build "$SRC/build" -j --target whisper-cli > /dev/null

mkdir -p "$CODE/.models"
if [ ! -f "$CODE/.models/$MODEL" ]; then
  curl -sfL -o "$CODE/.models/$MODEL.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL"
  mv "$CODE/.models/$MODEL.part" "$CODE/.models/$MODEL"
fi
echo "$MODEL_SHA256  $CODE/.models/$MODEL" | sha256sum -c --quiet || { echo "model hash mismatch" >&2; exit 1; }
echo "whisper-cli: $SRC/build/bin/whisper-cli"
echo "model: $CODE/.models/$MODEL"
