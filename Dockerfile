# Telegram Media Downloader sob demanda (docker run --rm).
#
# Antes: mkdir -p ~/tmd-data ~/dl   (senão o Docker cria as pastas como root)
# Coloque o config.json (api_id e api_hash) em ~/tmd-data.
#
#   docker build -t tmd .
#
#   # 1) login, uma vez (pede telefone e código; saia no menu)
#   docker run -it --rm --user "$(id -u):$(id -g)" -v ~/tmd-data:/data -v ~/dl:/downloads tmd
#
#   # 2) escolher os canais (grava ~/tmd-data/ultimo_lote.json)
#   docker run -it --rm --user "$(id -u):$(id -g)" -v ~/tmd-data:/data -v ~/dl:/downloads tmd --prepare
#
#   # 3) baixar o lote em ~/dl, sem terminal
#   docker run --rm --user "$(id -u):$(id -g)" -v ~/tmd-data:/data -v ~/dl:/downloads tmd --resume
#
# Segredo e estado ficam nos volumes, nunca na imagem.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_downloader.py google_drive_upload.py ./

ENV TMD_DATA_DIR=/data
VOLUME ["/data", "/downloads"]

ENTRYPOINT ["python", "telegram_downloader.py"]
