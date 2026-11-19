#!/usr/bin/env python3
"""
Telegram Group Downloader
Baixa vídeos e arquivos de grupos do Telegram via API (MTProto).
"""

import argparse
import atexit
import os
import re
import sys
import unicodedata
import asyncio
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

import questionary
from prompt_toolkit.keys import Keys
from telethon import TelegramClient, errors
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    InputMessagesFilterDocument,
    InputMessagesFilterPhotos,
    InputMessagesFilterVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
)

SESSION_NAME = "telegram_session"
CONFIG_FILE = Path(__file__).parent / "config.json"
LOG_FILE = Path(__file__).parent / "download_log.json"
COURSE_STRUCTURE_FILE = Path(__file__).parent / "course_structure.json"

MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 5
MAX_RETRY_DELAY = 300
MAX_CONCURRENT_DOWNLOADS = 10
DOWNLOAD_DELAY = 1.0
LIMITE_VAZAO_BPS = 0.5 * 1024 * 1024
JANELA_VAZAO_S = 600
MIN_ATIVOS_VAZAO = 5

logger = logging.getLogger(__name__)
LOGS_DIR = Path(__file__).parent / "logs"


def setup_logging() -> Path:
    """Cria um arquivo de log novo para esta execução (logs/downloader_AAAAMMDD_HHMMSS.log).

    Arquivo: INFO+ (grupos, opções, cada arquivo baixado, erros). Terminal: só WARNING+.
    """
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"downloader_{time.strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[file_handler, console_handler],
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logger.info("Execução iniciada (log: %s)", log_path.name)
    atexit.register(logger.info, "Execução encerrada")
    return log_path


def log_selection(queue, media_label: str, limit, destination: str, course_structure, post_actions):
    """Registra no log os grupos e as opções escolhidas para este lote de downloads."""
    logger.info("=" * 60)
    logger.info("Novo lote de downloads: %d grupo(s)", len(queue))
    for n, g in enumerate(queue, 1):
        logger.info("  Grupo %d/%d: %s", n, len(queue), g.name)
    logger.info("Mídia: %s", media_label)
    logger.info("Limite de mensagens: %s", limit if limit else "todas")
    logger.info("Destino: %s", destination)
    logger.info("Organização por pastas (course_structure): %s", "sim" if course_structure else "não")
    logger.info("Downloads simultâneos: %d", MAX_CONCURRENT_DOWNLOADS)
    logger.info("Ação ao terminar: %s", ", ".join(POST_ACTIONS[k] for k in sorted(post_actions)))
    logger.info("=" * 60)

MEDIA_FILTERS = {
    "1": {"label": "Apenas vídeos", "types": ["video"]},
    "2": {"label": "Apenas fotos", "types": ["photo"]},
    "3": {"label": "Apenas documentos/arquivos", "types": ["document"]},
    "4": {"label": "Tudo (vídeos + fotos + documentos)", "types": ["video", "photo", "document"]},
}


def load_course_structure(file_path: Path = COURSE_STRUCTURE_FILE) -> dict | None:
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Falha ao carregar estrutura do curso: %s", e)
        return None


def resolve_folder_from_message(message_text: str | None, structure: dict) -> str | None:
    if not message_text:
        return None

    tags = re.findall(r'#([A-Za-z]+)(\d+)', message_text)
    if not tags:
        return None

    for tag_prefix, tag_number_str in tags:
        tag_number = int(tag_number_str)
        for section in structure.get("sections", []):
            if section["prefix"].lower() != tag_prefix.lower():
                continue
            start, end = section["range"]
            if start <= tag_number <= end:
                return section["folder"]

    return None


def load_credentials() -> tuple[str, str]:
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text())
        api_id = data.get("api_id")
        api_hash = data.get("api_hash")
        if api_id and api_hash:
            return str(api_id), api_hash

    print(f"{'='*55}")
    print("  CONFIGURAÇÃO INICIAL")
    print(f"{'='*55}")
    print()
    print("  Para usar este script, você precisa de credenciais")
    print("  da API do Telegram. Siga os passos:")
    print()
    print("  1. Acesse: https://my.telegram.org")
    print("  2. Faça login com seu número de telefone")
    print("  3. Clique em 'API development tools'")
    print("  4. Crie um aplicativo (qualquer nome serve)")
    print("  5. Copie o 'api_id' e o 'api_hash'")
    print()
    print(f"{'='*55}\n")

    try:
        api_id = input("👉 Cole seu api_id: ").strip()
        api_hash = input("👉 Cole seu api_hash: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado.")
        sys.exit(0)

    if not api_id or not api_hash:
        print("❌ api_id e api_hash são obrigatórios.")
        sys.exit(1)

    CONFIG_FILE.write_text(json.dumps({"api_id": api_id, "api_hash": api_hash}, indent=2))
    print(f"\n✅ Credenciais salvas em {CONFIG_FILE.name}\n")

    return api_id, api_hash


def format_size(size_bytes: int) -> str:
    if size_bytes is None:
        return "tamanho desconhecido"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def calcular_rate(size: int, segundos: float) -> int:
    """Taxa média em bytes/s; 0 se o tamanho ou a duração não forem positivos."""
    if size <= 0 or segundos <= 0:
        return 0
    return int(size / segundos)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name[:255]


def check_cryptg():
    try:
        import cryptg
        return True
    except ImportError:
        return False


def _default_file_name(message) -> str | None:
    if not message.media:
        return None

    if isinstance(message.media, MessageMediaPhoto):
        return sanitize_filename(f"foto_{message.id}.jpg")

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if doc is None:
            return None

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                stem = Path(attr.file_name).stem
                suffix = Path(attr.file_name).suffix
                return sanitize_filename(f"{stem}_msg{message.id}{suffix}")

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                ext = "mp4"
                mime = getattr(doc, "mime_type", "")
                if "webm" in mime:
                    ext = "webm"
                elif "mkv" in mime:
                    ext = "mkv"
                return sanitize_filename(f"video_{message.id}.{ext}")

        mime = getattr(doc, "mime_type", "")
        ext = mime.split("/")[-1] if "/" in mime else "bin"
        return sanitize_filename(f"arquivo_{message.id}.{ext}")

    return None


def get_file_name(message) -> str | None:
    default = _default_file_name(message)
    if default is None or isinstance(message.media, MessageMediaPhoto):
        return default

    caption = next(
        (ln.strip() for ln in (message.message or "").splitlines() if ln.strip()), ""
    )
    if not caption:
        return default

    tail = f"_msg{message.id}{Path(default).suffix}"
    stem = sanitize_filename(caption)
    while len((stem + tail).encode("utf-8")) > 255:
        stem = stem[:-1]
    return sanitize_filename(stem + tail)


def classify_media(message) -> str | None:
    if not message.media:
        return None

    if isinstance(message.media, MessageMediaPhoto):
        return "photo"

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if doc is None:
            return None

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return "video"

        return "document"

    return None


class MonitorVazao:
    """Detecta rate limit: vazão agregada abaixo do limite numa janela deslizante, com downloads suficientes ativos."""

    BUCKET_S = 60

    def __init__(self, limite_bps: float = LIMITE_VAZAO_BPS, janela_s: float = JANELA_VAZAO_S,
                 min_ativos: int = MIN_ATIVOS_VAZAO, relogio=time.time):
        self.limite_bps = limite_bps
        self.janela_s = janela_s
        self.min_ativos = min_ativos
        self.relogio = relogio
        self.avisado = False
        self.vazao_bps = 0.0
        self._amostras: list[tuple[float, int, int]] = []
        self._t0: float | None = None
        self._disparou = False

    def registrar(self, delta_bytes: int, ativos: int) -> None:
        agora = self.relogio()
        if self._t0 is None:
            self._t0 = agora
        self._amostras.append((agora, delta_bytes, ativos))
        corte = agora - self.janela_s
        while self._amostras and self._amostras[0][0] < corte:
            self._amostras.pop(0)

    def lento(self) -> bool:
        if self._disparou:
            return True
        agora = self.relogio()
        if self._t0 is None or agora - self._t0 < self.janela_s:
            return False
        total_buckets = max(1, int(self.janela_s // self.BUCKET_S))
        buckets_ativos = set()
        bytes_ativos = 0
        for instante, delta, ativos in self._amostras:
            if instante < agora - self.janela_s or ativos < self.min_ativos:
                continue
            buckets_ativos.add(min(total_buckets - 1, int((agora - instante) // self.BUCKET_S)))
            bytes_ativos += delta
        if len(buckets_ativos) * 2 < total_buckets:
            return False
        self.vazao_bps = bytes_ativos / (len(buckets_ativos) * self.BUCKET_S)
        if self.vazao_bps < self.limite_bps:
            self._disparou = True
            return True
        return False


class LiveProgress:
    """Bloco fixo no fim do terminal com uma linha (barra, %, velocidade, ETA) por download ativo."""

    BAR_LENGTH = 20
    MIN_REDRAW_INTERVAL = 0.2
    SPEED_SMOOTHING = 0.3

    def __init__(self):
        self.active: dict[int, dict] = {}
        self._next_id = 0
        self._drawn_lines = 0
        self._last_draw = 0.0
        self.enabled = sys.stdout.isatty()
        self.monitor = None

    def start(self, name: str):
        self._next_id += 1
        task_id = self._next_id
        now = time.time()
        self.active[task_id] = {
            "name": name, "current": 0, "total": 0,
            "last_time": now, "last_bytes": 0, "speed": 0.0, "reportado": 0,
        }
        self._draw(force=True)

        def callback(current, total):
            self._update(task_id, current, total)

        return task_id, callback

    def finish(self, task_id: int):
        self.active.pop(task_id, None)
        self._draw(force=True)

    def log(self, text: str = ""):
        """print() que não quebra o bloco de progresso."""
        self._clear()
        print(text)
        self._draw(force=True)

    def _update(self, task_id: int, current: int, total: int):
        task = self.active.get(task_id)
        if self.monitor and task:
            self.monitor.registrar(current - task["reportado"], len(self.active))
            task["reportado"] = current
        if task is None or total <= 0:
            return
        now = time.time()
        task["current"], task["total"] = current, total
        interval = now - task["last_time"]
        if interval >= 0.5:
            sample = (current - task["last_bytes"]) / interval
            task["speed"] = (
                sample if task["speed"] == 0
                else self.SPEED_SMOOTHING * sample + (1 - self.SPEED_SMOOTHING) * task["speed"]
            )
            task["last_time"], task["last_bytes"] = now, current
        self._draw()

    def _format_line(self, task: dict, width: int) -> str:
        current, total, speed = task["current"], task["total"], task["speed"]
        if total > 0:
            filled = int(self.BAR_LENGTH * current // total)
            bar = "█" * filled + "░" * (self.BAR_LENGTH - filled)
            percent = f"{current / total * 100:5.1f}%"
            speed_str = f"{format_size(int(speed))}/s" if speed > 0 else "..."
            eta = time.strftime("%M:%S", time.gmtime((total - current) / speed)) if speed > 0 else "--:--"
            info = f"[{bar}] {percent} {format_size(current)}/{format_size(total)} {speed_str} ETA {eta}"
        else:
            info = f"[{'░' * self.BAR_LENGTH}]   0.0% iniciando..."
        name_width = max(10, width - len(info) - 4)
        name = task["name"]
        if len(name) > name_width:
            name = name[: name_width - 1] + "…"
        return f"  {name.ljust(name_width)} {info}"[: width - 1]

    def _clear(self):
        if self.enabled and self._drawn_lines:
            sys.stdout.write(f"\033[{self._drawn_lines}F\033[J")
            self._drawn_lines = 0

    def _draw(self, force: bool = False):
        if not self.enabled:
            return
        now = time.time()
        if not force and now - self._last_draw < self.MIN_REDRAW_INTERVAL:
            return
        self._last_draw = now
        self._clear()
        width = shutil.get_terminal_size((100, 24)).columns
        lines = [self._format_line(t, width) for t in self.active.values()]
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")
            self._drawn_lines = len(lines)
        sys.stdout.flush()


live = LiveProgress()


def load_download_log() -> dict:
    if LOG_FILE.exists():
        try:
            log = json.loads(LOG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

        # Migração do formato antigo para o novo (message_id como chave)
        for key, value in log.items():
            if isinstance(value, dict) and "downloaded" not in value:
                old_failed = value.get("failed_files", [])
                new_failed = {}
                for fname in old_failed:
                    new_failed[fname] = {"file": fname, "error": "migrado do formato antigo"}
                log[key] = {
                    "downloaded": {},
                    "failed": new_failed,
                    "stats": {
                        "completed": value.get("completed", 0),
                        "errors": value.get("errors", len(old_failed)),
                    },
                }

        return log
    return {}


def save_download_log(log: dict):
    try:
        tmp = LOG_FILE.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(log, indent=2, ensure_ascii=False))
        os.replace(str(tmp), str(LOG_FILE))
    except Exception as e:
        logger.warning("Falha ao salvar log de download: %s", e)


def registrar_scan(entrada: dict, total: int, pendentes: int):
    """Guarda no log do grupo o resultado do último scan (usado para colorir o menu)."""
    entrada["scan"] = {"total": total, "pendentes": pendentes, "em": time.strftime("%Y-%m-%d %H:%M:%S")}


def status_grupo(entrada: dict | None) -> str:
    """'completo' (verde), 'parcial' (amarelo) ou 'nada' (sem cor), pelo log e pelo último scan."""
    if not entrada or not entrada.get("downloaded"):
        return "nada"
    falhas = [k for k in entrada.get("failed", {}) if k not in entrada["downloaded"]]
    scan = entrada.get("scan")
    if scan and scan.get("pendentes") == 0 and not falhas:
        return "completo"
    return "parcial"


async def contar_grupo(client: TelegramClient, group, entrada: dict) -> tuple[int, int]:
    """Só leitura: (total de mídias do grupo, quantas ainda não estão no log). Não toca no disco."""
    baixados = entrada.get("downloaded", {})
    total = pendentes = 0
    async for message in client.iter_messages(group.entity):
        if classify_media(message) is None or get_file_name(message) is None:
            continue
        total += 1
        if str(message.id) not in baixados:
            pendentes += 1
    return total, pendentes


async def completar_scans_legados(client: TelegramClient, groups, download_log: dict):
    """Grupos baixados antes do campo 'scan' existir: conta uma vez para o menu pintá-los certo."""
    faltam = [
        g for g in groups
        if download_log.get(sanitize_filename(g.name), {}).get("downloaded")
        and "scan" not in download_log[sanitize_filename(g.name)]
    ]
    if not faltam:
        return
    print(f"🎨 Contando mídias de {len(faltam)} grupo(s) já baixados para colorir o menu (só nesta vez)...")
    for n, g in enumerate(faltam, 1):
        entrada = download_log[sanitize_filename(g.name)]
        print(f"   [{n}/{len(faltam)}] {g.name}...", end="", flush=True)
        try:
            total, pendentes = await contar_grupo(client, g, entrada)
        except Exception as e:
            logger.warning("Falha ao contar mídias de '%s': %s", g.name, e)
            print(" falhou, fica amarelo.")
            continue
        registrar_scan(entrada, total, pendentes)
        save_download_log(download_log)
        print(f" {total} mídias, {pendentes} pendentes.")
    print()



def check_disk_space(path: Path, required_bytes: int) -> bool:
    free = shutil.disk_usage(path).free
    if free < required_bytes * 1.1:
        print(f"\n  ⚠  Espaço em disco pode ser insuficiente!")
        print(f"     Necessário: ~{format_size(required_bytes)}")
        print(f"     Disponível: {format_size(free)}")
        return False
    return True


async def download_with_retry(
    client: TelegramClient, entity, message_id: int, file_path: Path, expected_size: int | None,
    drive_service=None, drive_folder_id: str | None = None,
    file_name: str | None = None,
) -> bool:
    is_drive = drive_service is not None and drive_folder_id is not None
    attempt = 1
    while attempt <= MAX_RETRIES:
        writer = None
        task_id = None
        try:
            fresh_message = await client.get_messages(entity, ids=message_id)
            if fresh_message is None:
                logger.warning("Mensagem %d não encontrada ao re-buscar", message_id)
                live.log(f"\n  ⚠  Mensagem {message_id} não encontrada no grupo")
                return False

            task_id, progress_cb = live.start(file_name or file_path.name)

            if is_drive:
                from google_drive_upload import GoogleDriveWriter, get_mime_type
                mime = get_mime_type(file_name or "arquivo.bin")
                upload_uri = drive_service.initiate_resumable_upload(
                    file_name, mime, drive_folder_id, expected_size
                )
                writer = GoogleDriveWriter(drive_service, upload_uri, expected_size)
                result = await client.download_media(
                    fresh_message,
                    file=writer,
                    progress_callback=progress_cb,
                )
                writer.close()

                if writer.tell() == 0:
                    logger.warning("Download vazio (Drive): %s (tentativa %d/%d)", file_name, attempt, MAX_RETRIES)
                    if attempt < MAX_RETRIES:
                        delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                        live.log(f"\n  ⚠  Download vazio: {file_name}")
                        live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
                        await asyncio.sleep(delay)
                    attempt += 1
                    continue

                if not writer._finalized:
                    logger.warning("Upload não finalizado (Drive): %s (tentativa %d/%d)", file_name, attempt, MAX_RETRIES)
                    if attempt < MAX_RETRIES:
                        delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                        live.log(f"\n  ⚠  Upload não concluído: {file_name}")
                        live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
                        await asyncio.sleep(delay)
                    attempt += 1
                    continue

                return True

            # Modo local (original)
            result = await client.download_media(
                fresh_message,
                file=str(file_path),
                progress_callback=progress_cb,
            )

            if result is None:
                if file_path.exists():
                    file_path.unlink()
                logger.warning("download_media retornou None: %s (tentativa %d/%d)", file_path.name, attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                    live.log(f"\n  ⚠  Download silencioso falhou: {file_path.name}")
                    live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
                    await asyncio.sleep(delay)
                attempt += 1
                continue

            result_path = Path(result)
            if not result_path.exists():
                logger.warning("Arquivo não encontrado após download: %s (tentativa %d/%d)", result_path.name, attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                    live.log(f"\n  ⚠  Arquivo não encontrado: {file_path.name}")
                    live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
                    await asyncio.sleep(delay)
                attempt += 1
                continue

            if result_path != file_path:
                result_path.rename(file_path)

            if file_path.stat().st_size == 0:
                logger.warning("Arquivo vazio após download: %s", file_path.name)
                file_path.unlink()
                if attempt < MAX_RETRIES:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                    live.log(f"\n  ⚠  Arquivo vazio: {file_path.name}")
                    live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
                    await asyncio.sleep(delay)
                attempt += 1
                continue

            return True

        except asyncio.CancelledError:
            if not is_drive and file_path.exists():
                file_path.unlink()
                live.log(f"\n  🗑  Arquivo incompleto removido: {file_path.name}")
            raise

        except errors.FloodWaitError as e:
            wait_time = e.seconds + 5
            live.log(f"\n  ⏳ Flood wait do Telegram: aguardando {wait_time}s antes de retentar...")
            logger.warning("FloodWaitError: aguardando %ds", wait_time)
            if not is_drive and file_path.exists():
                file_path.unlink()
            await asyncio.sleep(wait_time)
            # FloodWait NÃO consome tentativa

        except (ConnectionError, TimeoutError, OSError) as e:
            delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
            live.log(f"\n  🔌 Erro de conexão: {e}")
            live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
            logger.warning("Erro de conexão: %s (tentativa %d/%d, retry em %ds)", e, attempt, MAX_RETRIES, delay)
            if not is_drive and file_path.exists():
                file_path.unlink()
            await asyncio.sleep(delay)
            attempt += 1

        except Exception as e:
            delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
            live.log(f"\n  ❌ Erro inesperado: {e}")
            live.log(f"     Tentativa {attempt}/{MAX_RETRIES} — retentando em {delay}s...")
            logger.warning("Erro inesperado: %s (tentativa %d/%d)", e, attempt, MAX_RETRIES)
            if not is_drive and file_path.exists():
                file_path.unlink()
            await asyncio.sleep(delay)
            attempt += 1

        finally:
            if task_id is not None:
                live.finish(task_id)

    display_name = file_name if is_drive else file_path.name
    logger.error("Falha permanente após %d tentativas: %s", MAX_RETRIES, display_name)
    return False


async def list_groups(client: TelegramClient) -> list:
    groups = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, (Channel, Chat)):
            groups.append(dialog)
    return groups


TELETHON_FILTERS = {
    "video": InputMessagesFilterVideo,
    "photo": InputMessagesFilterPhotos,
    "document": InputMessagesFilterDocument,
}


async def scan_group_media(
    client: TelegramClient, group, media_types: list[str], limit: int | None,
    download_dir: Path, download_log: dict, group_log_key: str,
    course_structure: dict | None = None,
    drive_service=None, drive_group_folder_id: str | None = None,
):
    """Escaneia mensagens UMA vez, retornando stats e lista de pendentes."""
    total_size = 0
    counts = {"video": 0, "photo": 0, "document": 0}
    pending = []
    skipped = 0
    is_drive = drive_service is not None and drive_group_folder_id is not None

    # Cache de arquivos existentes no Drive (por pasta)
    drive_folder_files: dict[str, dict[str, dict]] = {}

    print(f"📊 Escaneando grupo '{group.name}'...", end="", flush=True)

    telethon_filter = None
    if len(media_types) == 1:
        telethon_filter = TELETHON_FILTERS.get(media_types[0])

    # Contagem instantânea quando há filtro server-side
    if telethon_filter:
        quick_count = await client.get_messages(group.entity, 0, filter=telethon_filter())
        if quick_count.total == 0:
            print(f" 0 {media_types[0]}s encontrados.\n")
            return {"total_size": 0, "counts": counts}, [], 0
        print(f" ~{quick_count.total} encontrados...", end="", flush=True)

    success = False
    for attempt_num in range(1, MAX_RETRIES + 1):
        try:
            async for message in client.iter_messages(
                group.entity, limit=limit, reverse=True,
                filter=telethon_filter
            ):
                media_type = classify_media(message)
                if media_type is None or media_type not in media_types:
                    continue

                counts[media_type] += 1
                file_name = get_file_name(message)
                if file_name is None:
                    continue

                expected_size = None
                if isinstance(message.media, MessageMediaDocument) and message.media.document:
                    expected_size = message.media.document.size or 0
                    total_size += expected_size

                # Verificar no log se já foi baixado
                msg_id_str = str(message.id)
                if msg_id_str in download_log.get(group_log_key, {}).get("downloaded", {}):
                    skipped += 1
                    continue

                if is_drive:
                    # Modo Drive: resolver pasta e verificar existência no Drive
                    if course_structure:
                        subfolder = resolve_folder_from_message(message.text, course_structure)
                        folder_name = subfolder if subfolder else "_sem_classificacao"
                        drive_folder_id = drive_service.get_or_create_folder(folder_name, drive_group_folder_id)
                    else:
                        drive_folder_id = drive_group_folder_id

                    # Cache de listagem por pasta (1 request por pasta, não por arquivo)
                    if drive_folder_id not in drive_folder_files:
                        drive_folder_files[drive_folder_id] = drive_service.list_files_in_folder(drive_folder_id)

                    existing = drive_folder_files[drive_folder_id].get(file_name)
                    if existing:
                        if expected_size and existing["size"] != expected_size:
                            pass  # Tamanho diferente, re-baixar
                        elif existing["size"] == 0:
                            pass  # Arquivo vazio, re-baixar
                        else:
                            skipped += 1
                            continue

                    pending.append((message.id, media_type, file_name, drive_folder_id, expected_size))
                else:
                    # Modo local: resolver subpasta no disco
                    if course_structure:
                        subfolder = resolve_folder_from_message(message.text, course_structure)
                        if subfolder:
                            target_dir = download_dir / subfolder
                            target_dir.mkdir(parents=True, exist_ok=True)
                        else:
                            target_dir = download_dir / "_sem_classificacao"
                            target_dir.mkdir(parents=True, exist_ok=True)
                    else:
                        target_dir = download_dir

                    file_path = target_dir / file_name

                    if file_path.exists():
                        if expected_size and file_path.stat().st_size != expected_size:
                            file_path.unlink()
                        elif file_path.stat().st_size == 0:
                            file_path.unlink()
                        else:
                            skipped += 1
                            continue

                    pending.append((message.id, media_type, file_name, file_path, expected_size))

            success = True
            break
        except errors.FloodWaitError as e:
            wait_time = e.seconds + 5
            print(f"\n  ⏳ Flood wait: aguardando {wait_time}s...")
            await asyncio.sleep(wait_time)
            total_size = 0
            counts = {"video": 0, "photo": 0, "document": 0}
            pending = []
            skipped = 0
            drive_folder_files.clear()
        except (ConnectionError, TimeoutError, OSError) as e:
            delay = min(INITIAL_RETRY_DELAY * (2 ** (attempt_num - 1)), MAX_RETRY_DELAY)
            print(f"\n  🔌 Erro: {e}, retentando em {delay}s...")
            await asyncio.sleep(delay)
            total_size = 0
            counts = {"video": 0, "photo": 0, "document": 0}
            pending = []
            skipped = 0
            drive_folder_files.clear()

    if not success:
        print(" FALHOU!\n")
        return None, [], 0

    print(" OK!\n")
    return {"total_size": total_size, "counts": counts}, pending, skipped


async def download_media_from_group(
    client: TelegramClient, group, media_types: list[str], limit: int | None,
    base_dir: Path | None = None, course_structure: dict | None = None,
    drive_service=None, drive_root_folder_id: str | None = None,
    interactive: bool = True, monitor: MonitorVazao | None = None,
) -> int:
    """Baixa a mídia de um grupo. Retorna o número de falhas (0 = tudo certo).

    Com interactive=False (modo fila) não pede confirmação: segue direto.
    """
    group_name = sanitize_filename(group.name)
    is_drive = drive_service is not None and drive_root_folder_id is not None

    if is_drive:
        download_dir = Path.cwd()  # Placeholder, não será usado para salvar arquivos
        drive_group_folder_id = drive_service.get_or_create_folder(group_name, drive_root_folder_id)
    else:
        if base_dir is None:
            base_dir = Path.cwd() / "downloads"
        download_dir = base_dir / group_name
        download_dir.mkdir(parents=True, exist_ok=True)
        drive_group_folder_id = None

    # Inicializar log ANTES do scan (o scan precisa consultá-lo)
    download_log = load_download_log()
    group_log_key = group_name
    if group_log_key not in download_log:
        download_log[group_log_key] = {"downloaded": {}, "failed": {}, "stats": {"completed": 0, "errors": 0}}

    if course_structure:
        num_sections = len(course_structure.get("sections", []))
        print(f"📁 Estrutura de pastas ativa: {num_sections} seções configuradas\n")

    stats, pending, skipped = await scan_group_media(
        client, group, media_types, limit, download_dir, download_log, group_log_key,
        course_structure, drive_service, drive_group_folder_id,
    )
    if stats is None:
        logger.error("Grupo '%s': falha ao escanear", group.name)
        print("❌ Falha ao escanear o grupo. Verifique sua conexão.\n")
        return 1

    total_items = sum(stats["counts"].values())
    registrar_scan(download_log[group_log_key], total_items, len(pending))
    save_download_log(download_log)
    logger.info(
        "Grupo '%s': %d vídeos, %d fotos, %d documentos (~%s) | %d para baixar, %d já existiam",
        group.name, stats["counts"]["video"], stats["counts"]["photo"], stats["counts"]["document"],
        format_size(stats["total_size"]), len(pending), skipped,
    )
    if total_items == 0:
        print("❌ Nenhuma mídia encontrada com os filtros selecionados.\n")
        return 0

    print(f"  {'='*45}")
    print(f"  RESUMO DO GRUPO")
    print(f"  {'='*45}")
    if stats["counts"]["video"]:
        print(f"  🎬 Vídeos:     {stats['counts']['video']}")
    if stats["counts"]["photo"]:
        print(f"  🖼  Fotos:      {stats['counts']['photo']}")
    if stats["counts"]["document"]:
        print(f"  📄 Documentos: {stats['counts']['document']}")
    print(f"  📦 Total:      {total_items} arquivos")
    print(f"  💾 Tamanho:    ~{format_size(stats['total_size'])} (sem contar fotos)")
    is_parallel = MAX_CONCURRENT_DOWNLOADS > 1
    if is_parallel:
        print(f"  🚀 Modo:       {MAX_CONCURRENT_DOWNLOADS} downloads simultâneos")
    if is_drive:
        print(f"  ☁️  Destino:    Google Drive")
    print(f"  {'='*45}")

    if not is_drive and stats["total_size"] > 0 and not check_disk_space(download_dir, stats["total_size"]):
        if not interactive:
            print("⏭  Grupo pulado por falta de espaço em disco.\n")
            return 1
        try:
            cont = input("\n  Deseja continuar mesmo assim? (s/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelado.")
            return 0
        if cont != "s":
            print("⏭  Download cancelado.\n")
            return 0
    print()

    if not pending:
        print(f"✅ Todos os {skipped} arquivos já foram baixados!\n")
        return 0

    print(f"  📥 {len(pending)} para baixar | ⏭ {skipped} já existem\n")

    if interactive:
        try:
            confirma = input("👉 Deseja continuar com o download? (s/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelado.")
            return 0

        if confirma != "s":
            print("⏭  Download cancelado.\n")
            return 0

    if is_drive:
        print(f"\n☁️  Salvando no Google Drive\n")
    else:
        print(f"\n📂 Salvando em: {download_dir}\n")

    downloaded = 0
    error_count = 0
    start_time = time.time()

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    counter_lock = asyncio.Lock()

    async def download_one(item_index: int, message_id: int, media_type: str, file_name: str, file_path_or_folder_id, expected_size: int | None):
        nonlocal downloaded, error_count

        async with semaphore:
            if monitor is not None and monitor.lento():
                if not monitor.avisado:
                    monitor.avisado = True
                    aviso = (
                        f"⏸  Rate limit detectado (vazão {monitor.vazao_bps / 1024 / 1024:.2f} MB/s "
                        "na janela de 10 min): não inicio novos downloads"
                    )
                    logger.warning(aviso)
                    live.log(aviso)
                return

            if is_parallel:
                await asyncio.sleep(DOWNLOAD_DELAY)

            size_str = f" ({format_size(expected_size)})" if expected_size else ""
            logger.info("Iniciando [%d/%d] %s%s", item_index, len(pending), file_name, size_str)
            live.log(f"⬇  [{item_index}/{len(pending)}] [{media_type.upper()}] {file_name}{size_str}")

            inicio = time.time()
            if is_drive:
                success = await download_with_retry(
                    client, group.entity, message_id, None, expected_size,
                    drive_service=drive_service, drive_folder_id=file_path_or_folder_id,
                    file_name=file_name,
                )
            else:
                success = await download_with_retry(
                    client, group.entity, message_id, file_path_or_folder_id, expected_size,
                )

            duracao = time.time() - inicio
            async with counter_lock:
                if success:
                    downloaded += 1
                    if is_drive:
                        size = expected_size or 0
                    else:
                        size = file_path_or_folder_id.stat().st_size
                    download_log[group_log_key]["downloaded"][str(message_id)] = {
                        "file": file_name,
                        "size": size,
                        "rate": calcular_rate(size, duracao),
                    }
                    download_log[group_log_key]["stats"]["completed"] = len(download_log[group_log_key]["downloaded"])
                    logger.info("Concluído (%d/%d): %s", downloaded, len(pending), file_name)
                    if is_parallel:
                        live.log(f"  ✅ {file_name} — concluído ({downloaded}/{len(pending)})")
                else:
                    error_count += 1
                    download_log[group_log_key]["failed"][str(message_id)] = {
                        "file": file_name,
                        "error": "falha permanente",
                    }
                    download_log[group_log_key]["stats"]["errors"] = len(download_log[group_log_key]["failed"])
                    live.log(f"  💀 Falha permanente após {MAX_RETRIES} tentativas: {file_name}")

                if (downloaded + error_count) % 5 == 0:
                    save_download_log(download_log)

    tasks = [
        download_one(i + 1, msg_id, mt, fn, fp_or_fid, es)
        for i, (msg_id, mt, fn, fp_or_fid, es) in enumerate(pending)
    ]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error("Exceção não tratada em task de download: %s", r)
    finally:
        registrar_scan(download_log[group_log_key], total_items, len(pending) - downloaded)
        save_download_log(download_log)
    elapsed = time.time() - start_time
    elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))

    logger.info(
        "Grupo '%s' finalizado: baixados=%d, pulados=%d, erros=%d, tempo=%s",
        group.name, downloaded, skipped, error_count, elapsed_str,
    )
    print(f"\n{'='*50}")
    print(f"✅ Baixados: {downloaded}")
    print(f"⏭  Já existiam (pulados): {skipped}")
    if error_count:
        print(f"❌ Erros (após {MAX_RETRIES} tentativas cada): {error_count}")
    print(f"⏱  Tempo total: {elapsed_str}")
    if is_drive:
        print(f"☁️  Destino: Google Drive")
    else:
        print(f"📂 Pasta: {download_dir}")
    return error_count


def normalizar_busca(texto: str) -> str:
    """Minúsculas e sem acento, para busca por trecho do nome."""
    sem_acento = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).casefold()


COR_STATUS = {"completo": "fg:ansigreen", "parcial": "fg:ansiyellow", "nada": ""}
ORDEM_STATUS = {"parcial": 0, "completo": 1, "nada": 2}


class _TituloBusca(list):
    """Título colorido do checkbox (lista de (estilo, texto)). O filtro do questionary compara
    com título.lower(); aqui .lower() devolve só o nome, sem acento, para a busca casar por trecho."""

    def lower(self):
        return self.busca


def montar_opcoes(groups, download_log: dict | None = None) -> list:
    """Uma opção por grupo: '[Canal|Grupo] Nome (N membros)', valor = índice na lista.
    O nome fica verde (baixado por completo), amarelo (baixado em parte) ou sem cor (nada baixado)."""
    download_log = download_log or {}
    opcoes = []
    status = [status_grupo(download_log.get(sanitize_filename(g.name or ""))) for g in groups]
    # amarelo, depois verde, depois sem cor; ordem original dentro de cada bloco
    ordem = sorted(range(len(groups)), key=lambda i: ORDEM_STATUS[status[i]])
    for i in ordem:
        g = groups[i]
        member_count = ""
        entity = g.entity
        if hasattr(entity, "participants_count") and entity.participants_count:
            member_count = f" ({entity.participants_count} membros)"
        tipo = "Canal" if isinstance(entity, Channel) and entity.broadcast else "Grupo"
        cor = COR_STATUS[status[i]]
        titulo = _TituloBusca([(cor, f"[{tipo}] {g.name}{member_count}")])
        titulo.busca = normalizar_busca(g.name or "")
        opcoes.append(questionary.Choice(title=titulo, value=i))
    return opcoes


def _marcar_com_seta_direita(pergunta):
    """No questionary o ESPAÇO marca e o filtro ignora espaço, o que impede buscar
    "curso de python". Move o toggle do ESPAÇO para a seta → e faz o ESPAÇO digitar
    no filtro, reaproveitando os handlers do próprio checkbox (ligado a questionary <2.2)."""
    bindings = pergunta.application.key_bindings
    handler = {b.keys: b.handler for b in bindings.bindings}
    marcar, digitar = handler[(" ",)], handler[("a",)]
    bindings.remove(" ")
    bindings.add(" ", eager=True)(digitar)
    bindings.add(Keys.Right, eager=True)(marcar)
    return pergunta


def _atalhos_de_pagina(pergunta):
    """PageUp/PageDown saltam uma tela; Home/End vão ao primeiro/último item da lista filtrada."""
    bindings = pergunta.application.key_bindings
    ic = next(w.content for w in pergunta.application.layout.find_all_windows()
              if type(w.content).__name__ == "InquirerControl")

    def ir_para(destino):
        ic.pointed_at = max(0, min(len(ic.filtered_choices) - 1, destino))

    pagina = lambda: max(1, shutil.get_terminal_size().lines - 6)
    bindings.add(Keys.PageDown, eager=True)(lambda e: ir_para(ic.pointed_at + pagina()))
    bindings.add(Keys.PageUp, eager=True)(lambda e: ir_para(ic.pointed_at - pagina()))
    bindings.add(Keys.Home, eager=True)(lambda e: ir_para(0))
    bindings.add(Keys.End, eager=True)(lambda e: ir_para(len(ic.filtered_choices) - 1))
    return pergunta


async def escolher_grupos(groups, download_log: dict | None = None) -> list | None:
    """Caixa de seleção dos grupos. Retorna os marcados na ordem da lista; None = sair."""
    try:
        marcados = await _atalhos_de_pagina(_marcar_com_seta_direita(questionary.checkbox(
            "Escolha o(s) grupo(s):",
            choices=montar_opcoes(groups, download_log),
            instruction="(setas, PgUp/PgDn, Home/End navegam; → marca; ENTER confirma; digite para filtrar; amarelo = parcial, verde = completo)",
            use_search_filter=True,
            use_jk_keys=False,
        ))).ask_async(kbi_msg="\nSaindo...")
    except EOFError:
        print("\nSaindo...")
        return None
    if not marcados:
        return None
    return [groups[i] for i in sorted(marcados)]


DESTINOS_FILE = Path(__file__).parent / "ultimos_destinos.json"
MAX_DESTINOS = 10
LOTE_FILE = Path(__file__).parent / "ultimo_lote.json"
_NOVO_DESTINO = object()


def carregar_destinos() -> list[str]:
    """Últimas pastas de destino usadas, a mais recente primeiro."""
    try:
        dados = json.loads(DESTINOS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(dados, list):
        return []
    return [d for d in dados if isinstance(d, str)][:MAX_DESTINOS]


def salvar_destino(destino: str):
    """Põe a pasta no topo da lista de últimos destinos (sem repetir)."""
    lista = [destino] + [d for d in carregar_destinos() if d != destino]
    try:
        tmp = DESTINOS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(lista[:MAX_DESTINOS], indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(DESTINOS_FILE))
    except OSError as e:
        logger.warning("Falha ao salvar últimos destinos: %s", e)


def novo_lote(media_choice: str, limit: int | None, destino: str | None, estrutura: bool,
              acoes: set[str], queue, drive: bool = False) -> dict:
    """Escolhas do lote e estado de cada grupo, para o --resume."""
    return {
        "iniciado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "midia": media_choice,
        "limite": limit,
        "destino": destino,
        "estrutura": estrutura,
        "acoes": sorted(acoes),
        "drive": drive,
        "grupos": [{"id": g.id, "nome": g.name, "estado": "pendente"} for g in queue],
    }


def salvar_lote(lote: dict):
    try:
        tmp = LOTE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(lote, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(LOTE_FILE))
    except OSError as e:
        logger.warning("Falha ao salvar o último lote: %s", e)


def carregar_lote() -> dict | None:
    """Último lote gravado; None se ausente, ilegível ou sem as chaves esperadas."""
    try:
        lote = json.loads(LOTE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    chaves = ("midia", "limite", "destino", "estrutura", "acoes", "drive", "grupos")
    if not isinstance(lote, dict) or not all(c in lote for c in chaves):
        return None
    return lote


def marcar_estado(lote: dict, group_id: int, estado: str):
    for item in lote["grupos"]:
        if item["id"] == group_id:
            item["estado"] = estado
    salvar_lote(lote)


def estado_final(errs: int | None, entrada_log: dict | None) -> str:
    """'concluido' só se a chamada voltou sem exceção, sem falhas e o último scan não deixou pendentes."""
    scan = (entrada_log or {}).get("scan")
    if errs == 0 and scan and scan.get("pendentes") == 0:
        return "concluido"
    return "interrompido"


def resolver_grupos(lote: dict, groups: list) -> tuple[list, list[str]]:
    """Acha os grupos não concluídos do lote: por ID, depois por nome exato (só se for único).

    Devolve (encontrados, ausentes). Atualiza id e nome no próprio lote com os do grupo achado.
    """
    encontrados, ausentes = [], []
    for item in lote["grupos"]:
        if item["estado"] == "concluido":
            continue
        achado = next((g for g in groups if g.id == item["id"]), None)
        if achado is None:
            nome = sanitize_filename(item["nome"])
            iguais = [g for g in groups if sanitize_filename(g.name or "") == nome]
            achado = iguais[0] if len(iguais) == 1 else None
        if achado is None:
            ausentes.append(item["nome"])
            continue
        item["id"], item["nome"] = achado.id, achado.name
        encontrados.append(achado)
    return encontrados, ausentes


def validar_retomada(lote: dict) -> str | None:
    """Mensagem de erro se o lote não pode ser retomado agora; None se pode seguir. Não cria nada em disco."""
    if lote["drive"]:
        return "O lote foi gravado com destino Google Drive, e o Drive não é retomado automaticamente."
    if lote["destino"] is not None and not Path(lote["destino"]).expanduser().exists():
        return f"A pasta de destino do lote não existe: {lote['destino']} (disco/SMB desmontado?)"
    if lote["estrutura"] and load_course_structure() is None:
        return "O lote usava organização por pastas, mas o course_structure.json não foi encontrado."
    return None


async def pedir_destino() -> str | None:
    """Pasta de destino: escolhe um dos últimos (setas) ou digita outro. '' = padrão; None = sair."""
    recentes = carregar_destinos()
    if recentes:
        try:
            escolha = await questionary.select(
                "Pasta de destino:",
                choices=[
                    *recentes,
                    questionary.Choice("✏  Digitar outro caminho", value=_NOVO_DESTINO),
                    questionary.Choice("📂 Padrão (./downloads)", value=""),
                ],
                use_jk_keys=False,
            ).ask_async(kbi_msg="\nSaindo...")
        except EOFError:
            escolha = None
        if escolha is None:
            print("\nSaindo...")
            return None
        if escolha is not _NOVO_DESTINO:
            return escolha
    print("Onde deseja salvar os arquivos?")
    print("  - Digite o caminho completo da pasta (ex: /home/usuario/cursos)")
    print("  - Ou pressione ENTER para usar o padrão (./downloads)\n")
    try:
        return input("👉 Pasta de destino: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nSaindo...")
        return None


POST_ACTIONS = {
    "1": "Nada (só sair)",
    "2": "Desligar a máquina",
    "3": "Suspender",
    "4": "Notificação desktop",
}


def ask_post_actions() -> set[str] | None:
    """Pergunta o que fazer ao fim. Retorna chaves de POST_ACTIONS ('1'..'4'); None = cancelado."""
    print("\nO que fazer quando terminar? (pode combinar, ex: 4,2)")
    for k, v in POST_ACTIONS.items():
        print(f"  {k}. {v}")
    try:
        raw = input("👉 Ação [1]: ").strip() or "1"
    except (KeyboardInterrupt, EOFError):
        return None
    chosen = {c for c in raw.replace(" ", "").split(",") if c}
    if not chosen or not chosen <= POST_ACTIONS.keys():
        print("  (opção inválida, usando: Nada)")
        return {"1"}
    if "2" in chosen and "3" in chosen:
        print("  (desligar e suspender juntos: usando só desligar)")
        chosen.discard("3")
    return chosen


def run_post_actions(actions: set[str], summary: str):
    if "4" in actions:
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "Telegram Downloader", summary], check=False)
        else:
            print("  ⚠  notify-send não encontrado (apt install libnotify-bin)")
    for key, cmd in (("2", ["systemctl", "poweroff"]), ("3", ["systemctl", "suspend"])):
        if key not in actions:
            continue
        label = POST_ACTIONS[key].lower()
        print(f"\n⚠️  Vou {label} em 60s. Ctrl+C para cancelar.")
        try:
            for remaining in range(60, 0, -1):
                print(f"\r   {remaining:2d}s ", end="", flush=True)
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n   ✋ {label.capitalize()} cancelado.")
            return
        print()
        subprocess.run(cmd, check=False)


async def executar_fila(client, queue: list, media_types: list[str], limit: int | None,
                        base_dir: Path | None, course_structure: dict | None,
                        drive_service, drive_root_folder_id, interativo: bool,
                        lote: dict | None = None,
                        monitor: MonitorVazao | None = None) -> tuple[list[str], list[str]]:
    """Baixa cada grupo da fila em ordem; devolve (nomes que falharam, nomes interrompidos pelo freio de rate limit).
    Com lote, registra o estado de cada grupo; grupo pulado pelo freio sem iniciar continua pendente."""
    monitor = monitor or MonitorVazao()
    live.monitor = monitor
    failed_groups: list[str] = []
    interrompidos: list[str] = []
    for pos, selected in enumerate(queue, 1):
        if monitor.lento():
            interrompidos.append(selected.name)
            continue
        if not interativo:
            print(f"\n{'#'*50}\n  FILA [{pos}/{len(queue)}]: {selected.name}\n{'#'*50}\n")
        errs = None
        if lote is not None:
            marcar_estado(lote, selected.id, "em_andamento")
        try:
            errs = await download_media_from_group(
                client, selected, media_types, limit, base_dir, course_structure,
                drive_service, drive_root_folder_id, interactive=interativo,
                monitor=monitor,
            )
            if errs:
                failed_groups.append(selected.name)
            if grupo_interrompido(monitor, selected.name, load_download_log()):
                interrompidos.append(selected.name)
        except (ConnectionError, TimeoutError, OSError) as e:
            failed_groups.append(selected.name)
            logger.error("Conexão perdida durante download: %s", e)
            print(f"\n🔌 Conexão perdida: {e}")
            print("   Tentando reconectar...")
            try:
                await client.connect()
                print("   ✅ Reconectado! Rode novamente para continuar de onde parou.\n")
            except Exception as re:
                logger.error("Falha ao reconectar: %s", re)
                print(f"   ❌ Falha ao reconectar: {re}")
                print("   Execute o script novamente para retomar.\n")
        except errors.FloodWaitError as e:
            failed_groups.append(selected.name)
            wait_time = e.seconds + 10
            print(f"\n⏳ Flood wait do Telegram: aguardando {wait_time}s...")
            logger.warning("FloodWaitError no loop principal: aguardando %ds", wait_time)
            await asyncio.sleep(wait_time)
            print("   ✅ Tempo de espera concluído. Rode novamente para continuar.\n")
        finally:
            if lote is not None:
                marcar_estado(
                    lote, selected.id,
                    estado_final(errs, load_download_log().get(sanitize_filename(selected.name))),
                )

    return failed_groups, interrompidos


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram Group Downloader")
    parser.add_argument("--resume", action="store_true",
                        help="retoma, sem perguntar nada, os grupos que não terminaram no último lote")
    return parser.parse_args(argv)


async def retomar(client) -> int:
    """Retoma o último lote com as mesmas escolhas. Códigos: 0 ok/nada a retomar, 1 ausente ou falha, 2 não dá para retomar."""
    lote = carregar_lote()
    if lote is None:
        print("❌ Nenhum lote anterior para retomar (ultimo_lote.json ausente ou inválido).")
        return 2

    erro = validar_retomada(lote)
    if erro:
        print(f"❌ {erro}")
        return 2

    print("🔄 Carregando seus grupos/canais...\n")
    groups = await list_groups(client)
    encontrados, ausentes = resolver_grupos(lote, groups)
    salvar_lote(lote)

    if not encontrados and not ausentes:
        print("✅ Tudo concluído no último lote.")
        return 0

    base_dir = Path(lote["destino"]).expanduser().resolve() if lote["destino"] else None
    course_structure = load_course_structure() if lote["estrutura"] else None
    limit = lote["limite"]
    acoes = set(lote["acoes"])

    print(f"♻️  Retomando o lote iniciado em {lote['iniciado_em']}")
    print(f"   Mídia: {MEDIA_FILTERS[lote['midia']]['label']}")
    print(f"   Limite: {limit if limit else 'todas'}")
    print(f"   Destino: {base_dir or Path.cwd() / 'downloads'}")
    print(f"   Grupos a retomar ({len(encontrados)}):")
    for n, g in enumerate(encontrados, 1):
        print(f"     {n}. {g.name}")
    for nome in ausentes:
        print(f"   ⚠  Grupo não encontrado, será pulado: {nome}")
    print()

    failed_groups: list[str] = []
    interrompidos: list[str] = []
    if encontrados:
        failed_groups, interrompidos = await executar_fila(
            client, encontrados, MEDIA_FILTERS[lote["midia"]]["types"], limit, base_dir,
            course_structure, None, None, interativo=False, lote=lote,
        )
        sem_falha = len(encontrados) - len(set(failed_groups) | set(interrompidos))
        if interrompidos:
            print(f"\n⏸  Interrompido por rate limit (rode de novo mais tarde):")
            for name in interrompidos:
                print(f"  ⏸  {name}")
        print(f"\n{'='*50}\n  RETOMADA CONCLUÍDA: {sem_falha}/{len(encontrados)} grupos sem falha")
        for name in failed_groups:
            print(f"  ❌ com falha: {name}")
        print(f"{'='*50}")

    if acoes != {"1"}:
        await client.disconnect()
        summary = f"Retomada concluída: {len(encontrados) - len(set(failed_groups) | set(interrompidos))}/{len(encontrados)} grupos sem falha"
        if interrompidos:
            summary += f" | {len(interrompidos)} interrompido(s) por rate limit"
        run_post_actions(acoes, summary)

    return 1 if ausentes or failed_groups or interrompidos else 0


def grupo_interrompido(monitor, nome_grupo: str, download_log: dict) -> bool:
    """True se o freio de rate limit disparou e o grupo ficou com pendências."""
    if not monitor.lento():
        return False
    entrada = download_log.get(sanitize_filename(nome_grupo)) or {}
    return entrada.get("scan", {}).get("pendentes", 0) > 0


async def main(argv: list[str] | None = None) -> int | None:
    args = parse_args(argv)

    setup_logging()
    api_id, api_hash = load_credentials()

    client = TelegramClient(
        SESSION_NAME,
        int(api_id),
        api_hash,
        connection_retries=10,
        request_retries=10,
        retry_delay=5,
        auto_reconnect=True,
        flood_sleep_threshold=120,
        timeout=30,
    )
    try:
        await client.start()
    except errors.PhoneNumberInvalidError:
        print("\n❌ Número de telefone inválido. Verifique o formato (ex: +5511999999999) e tente novamente.")
        return
    except errors.ApiIdInvalidError:
        print("\n❌ API ID ou API Hash inválidos. Verifique suas credenciais no arquivo .env.")
        return

    print(f"\n{'='*50}")
    print("  TELEGRAM GROUP DOWNLOADER")
    print(f"{'='*50}")

    if not check_cryptg():
        print("  ⚠  cryptg não instalado — downloads podem ser lentos")
        print("     Instale com: pip install cryptg\n")

    me = await client.get_me()
    print(f"  Logado como: {me.first_name} (@{me.username or 'sem username'})")
    print(f"{'='*50}\n")

    if args.resume:
        try:
            return await retomar(client)
        finally:
            await client.disconnect()

    while True:
        print("🔄 Carregando seus grupos/canais...\n")
        groups = await list_groups(client)

        if not groups:
            print("❌ Nenhum grupo ou canal encontrado.")
            break

        download_log = load_download_log()
        await completar_scans_legados(client, groups, download_log)
        queue = await escolher_grupos(groups, download_log)
        if not queue:
            break

        is_queue = len(queue) > 1
        if is_queue:
            print(f"\n✅ Fila ({len(queue)} grupos), mesma configuração para todos:")
            for n, g in enumerate(queue, 1):
                print(f"   {n}. {g.name}")
            print()
        else:
            print(f"\n✅ Selecionado: {queue[0].name}\n")

        print("Que tipo de mídia deseja baixar?\n")
        for key, val in MEDIA_FILTERS.items():
            print(f"  {key}. {val['label']}")
        print()

        try:
            media_choice = input("👉 Escolha (1-4): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSaindo...")
            break

        if media_choice not in MEDIA_FILTERS:
            print("❌ Opção inválida.\n")
            continue

        media_types = MEDIA_FILTERS[media_choice]["types"]

        print("\nQuantas mensagens verificar? (quanto maior, mais tempo leva)")
        print("  - Digite um número (ex: 100, 500, 1000)")
        print("  - Ou 'todas' para verificar todas\n")

        try:
            limit_input = input("👉 Limite: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nSaindo...")
            break

        limit = None if limit_input == "todas" else int(limit_input) if limit_input.isdigit() else 100

        if limit and not limit_input.isdigit():
            print(f"  (valor inválido, usando padrão: {limit})")

        # Opção Google Drive
        drive_service = None
        drive_root_folder_id = None

        print(f"\nDeseja enviar direto para o Google Drive? (sem usar disco local)")
        try:
            usar_drive = input("👉 Google Drive? (s/n) [n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nSaindo...")
            break

        if usar_drive == "s":
            from google_drive_upload import GoogleDriveService
            drive_service = GoogleDriveService()
            if not drive_service.authenticate():
                print("❌ Falha na autenticação com o Google Drive.\n")
                continue

            try:
                drive_folder_name = input("👉 Nome da pasta raiz no Drive [Telegram Downloads]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nSaindo...")
                break
            if not drive_folder_name:
                drive_folder_name = "Telegram Downloads"

            drive_root_folder_id = drive_service.get_or_create_folder(drive_folder_name)
            print(f"  ☁️  Pasta raiz no Drive: {drive_folder_name}\n")

        base_dir = None
        if drive_service is None:
            dir_input = await pedir_destino()
            if dir_input is None:
                break

            if dir_input:
                base_dir = Path(dir_input).expanduser().resolve()
                if not base_dir.exists():
                    try:
                        criar = input(f"  📁 A pasta '{base_dir}' não existe. Deseja criá-la? (s/n): ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print("\nSaindo...")
                        break
                    if criar != "s":
                        print("⏭  Download cancelado.\n")
                        continue
                salvar_destino(str(base_dir))
                print(f"  📂 Arquivos serão salvos em: {base_dir}/<nome do grupo>\n")

        # Verificar se existe estrutura de curso
        course_structure = None
        structure_data = load_course_structure()
        if structure_data:
            num_sections = len(structure_data.get("sections", []))
            print(f"📁 Arquivo course_structure.json encontrado ({num_sections} seções).")
            print(f"   Os downloads serão organizados em subpastas por módulo.\n")
            try:
                usar_estrutura = input("👉 Usar organização por pastas? (s/n): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nSaindo...")
                break
            if usar_estrutura == "s":
                course_structure = structure_data
                print("  ✅ Organização por pastas ativada!\n")
            else:
                print("  ⏭  Downloads serão salvos em pasta única.\n")

        post_actions = ask_post_actions()
        if post_actions is None:
            print("\nSaindo...")
            break

        if drive_service is not None:
            destination = f"Google Drive (pasta raiz: {drive_folder_name})"
        else:
            destination = str(base_dir or Path.cwd() / "downloads")
        log_selection(queue, MEDIA_FILTERS[media_choice]["label"], limit, destination, course_structure, post_actions)

        lote = novo_lote(
            media_choice, limit, str(base_dir) if base_dir else None, course_structure is not None,
            post_actions, queue, drive=drive_service is not None,
        )
        salvar_lote(lote)

        failed_groups, interrompidos = await executar_fila(
            client, queue, media_types, limit, base_dir, course_structure,
            drive_service, drive_root_folder_id, interativo=not is_queue, lote=lote,
        )

        sem_falha = len(queue) - len(set(failed_groups) | set(interrompidos))
        if interrompidos:
            print(f"\n⏸  Interrompido por rate limit (rode de novo mais tarde):")
            for name in interrompidos:
                print(f"  ⏸  {name}")

        if is_queue:
            print(f"\n{'='*50}\n  FILA CONCLUÍDA: {sem_falha}/{len(queue)} grupos sem falha")
            for name in failed_groups:
                print(f"  ❌ com falha: {name}")
            print(f"{'='*50}")

        if post_actions != {"1"}:
            await client.disconnect()
            summary = f"Download concluído: {sem_falha}/{len(queue)} grupos sem falha"
            if interrompidos:
                summary += f" | {len(interrompidos)} interrompido(s) por rate limit"
            run_post_actions(post_actions, summary)
            print("\n👋 Até mais!")
            return

        print()
        try:
            cont = input("Deseja baixar de outro grupo? (s/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if cont != "s":
            break

    await client.disconnect()
    print("\n👋 Até mais!")


if __name__ == "__main__":
    try:
        codigo = asyncio.run(main())
        sys.exit(codigo or 0)
    except KeyboardInterrupt:
        print("\n\n👋 Download interrompido. Execute novamente para continuar de onde parou.")
