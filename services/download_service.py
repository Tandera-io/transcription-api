import requests
import os
import tempfile
import logging
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, unquote
import mimetypes
import subprocess
import hashlib

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DownloadService:
    """Serviço melhorado para download de arquivos de vídeo e áudio"""
    
    def __init__(self):
        self.supported_formats = {
            'video': ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv'],
            'audio': ['.mp3', '.wav', '.aac', '.flac', '.ogg', '.m4a', '.wma']
        }
        self.max_file_size = 5 * 1024 * 1024 * 1024  # 5GB
        self.chunk_size = 8192  # 8KB chunks
        
        logger.info("Serviço de download inicializado")
    
    def _get_file_extension(self, url: str, content_type: Optional[str] = None) -> str:
        parsed_url = urlparse(url)
        path = unquote(parsed_url.path)
        if '.' in path:
            extension = os.path.splitext(path)[1].lower()
            if extension:
                return extension
        if content_type:
            extension = mimetypes.guess_extension(content_type)
            if extension:
                return extension.lower()
        return '.mp4'
    
    def _validate_file_format(self, file_path: str) -> Dict[str, Any]:
        try:
            extension = os.path.splitext(file_path)[1].lower()
            all_supported = self.supported_formats['video'] + self.supported_formats['audio']
            if extension not in all_supported:
                return {
                    "valid": False,
                    "error": f"Formato não suportado: {extension}. Formatos aceitos: {', '.join(all_supported)}"
                }
            file_size = os.path.getsize(file_path)
            if file_size > self.max_file_size:
                return {
                    "valid": False,
                    "error": f"Arquivo muito grande: {file_size} bytes. Máximo: {self.max_file_size} bytes"
                }
            media_type = 'video' if extension in self.supported_formats['video'] else 'audio'
            return {"valid": True, "media_type": media_type, "extension": extension, "file_size": file_size}
        except Exception as e:
            return {"valid": False, "error": f"Erro ao validar arquivo: {str(e)}"}
    
    def _extract_audio_from_video(self, video_path: str, output_path: str) -> Dict[str, Any]:
        try:
            possible_paths = [
                'ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg',
                '/app/.apt/usr/bin/ffmpeg', '/usr/bin/ffmpeg-static'
            ]
            ffmpeg_path = None
            for path in possible_paths:
                try:
                    result = subprocess.run([path, '-version'], capture_output=True, check=True, timeout=10)
                    ffmpeg_path = path
                    logger.info(f"FFmpeg encontrado em: {path}")
                    break
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            if not ffmpeg_path:
                logger.info("FFmpeg não encontrado, tentando instalar...")
                try:
                    for cmd in [['apt-get', 'update'], ['apt-get', 'install', '-y', 'ffmpeg']]:
                        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
                    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, timeout=10)
                    ffmpeg_path = 'ffmpeg'
                    logger.info("FFmpeg instalado com sucesso via apt-get")
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                    pass
            if not ffmpeg_path:
                return {"success": False, "error": "FFmpeg não está instalado ou não está disponível no PATH"}
            logger.info(f"[DEBUG] Iniciando extração de áudio de vídeo: {video_path} -> {output_path}")
            cmd = [ffmpeg_path, '-i', video_path, '-vn', '-acodec', 'libmp3lame', '-ab', '192k', '-ar', '44100', '-y', output_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                logger.info("Extração de áudio concluída com sucesso")
                return {"success": True, "audio_path": output_path}
            else:
                error_msg = f"Erro no ffmpeg: {result.stderr}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Timeout durante extração de áudio"}
        except Exception as e:
            return {"success": False, "error": f"Erro inesperado durante extração de áudio: {str(e)}"}
    
    def download_file(self, url: str, job_id: str) -> Dict[str, Any]:
        try:
            logger.info(f"Iniciando download: {url}")
            try:
                head_response = requests.head(url, timeout=30, allow_redirects=True)
                content_type = head_response.headers.get('content-type', '')
                content_length = head_response.headers.get('content-length')
                if content_length:
                    file_size = int(content_length)
                    if file_size > self.max_file_size:
                        return {"success": False, "error": f"Arquivo muito grande: {file_size} bytes. Máximo: {self.max_file_size} bytes"}
            except requests.exceptions.RequestException:
                content_type = None
            extension = self._get_file_extension(url, content_type)
            temp_dir = tempfile.gettempdir()
            original_file = os.path.join(temp_dir, f"{job_id}_original{extension}")
            with requests.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                total_size = 0
                with open(original_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if chunk:
                            f.write(chunk)
                            total_size += len(chunk)
                            if total_size > self.max_file_size:
                                os.remove(original_file)
                                return {"success": False, "error": "Arquivo excede tamanho máximo permitido"}
            logger.info(f"Download concluído: {original_file} ({total_size} bytes)")
            validation = self._validate_file_format(original_file)
            if not validation["valid"]:
                os.remove(original_file)
                return {"success": False, "error": validation["error"]}
            if validation["media_type"] == "video":
                audio_file = os.path.join(temp_dir, f"{job_id}_audio.mp3")
                extraction_result = self._extract_audio_from_video(original_file, audio_file)
                os.remove(original_file)
                if not extraction_result["success"]:
                    return extraction_result
                final_file = audio_file
            else:
                final_file = original_file
            file_hash = self._calculate_file_hash(final_file)
            return {"success": True, "file_path": final_file, "media_type": validation["media_type"], "file_size": os.path.getsize(final_file), "file_hash": file_hash}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout durante download do arquivo"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Erro na requisição de download: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Erro inesperado durante download: {str(e)}"}
    
    def _calculate_file_hash(self, file_path: str) -> str:
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    def cleanup_file(self, file_path: str) -> bool:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Arquivo removido: {file_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Erro ao remover arquivo {file_path}: {str(e)}")
            return False
    
    def get_file_info(self, url: str) -> Dict[str, Any]:
        try:
            response = requests.head(url, timeout=30, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '')
            content_length = response.headers.get('content-length')
            last_modified = response.headers.get('last-modified')
            extension = self._get_file_extension(url, content_type)
            return {"success": True, "url": url, "content_type": content_type, "file_size": int(content_length) if content_length else None, "extension": extension, "last_modified": last_modified, "supported": extension in (self.supported_formats['video'] + self.supported_formats['audio'])}
        except Exception as e:
            return {"success": False, "error": f"Erro ao obter informações do arquivo: {str(e)}"}
    
    def split_video_by_size(self, video_path: str, max_size_mb: int = 400) -> List[str]:
        import math
        import shutil
        max_size_bytes = max_size_mb * 1024 * 1024
        file_size = os.path.getsize(video_path)
        if file_size <= max_size_bytes:
            return [video_path]
        import subprocess
        import json as pyjson
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', video_path]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True)
        duration = float(pyjson.loads(result.stdout)['format']['duration'])
        num_parts = math.ceil(file_size / max_size_bytes)
        part_duration = duration / num_parts
        split_paths = []
        for i in range(num_parts):
            start = i * part_duration
            output_path = f"{video_path}.part{i+1}.mp4"
            ffmpeg_cmd = ['ffmpeg', '-y', '-i', video_path, '-ss', str(int(start)), '-t', str(int(part_duration)), '-c', 'copy', output_path]
            logger.info(f"Cortando parte {i+1}/{num_parts}: {' '.join(ffmpeg_cmd)}")
            subprocess.run(ffmpeg_cmd, capture_output=True, check=True)
            split_paths.append(output_path)
        try:
            os.remove(video_path)
        except Exception as e:
            logger.warning(f"Não foi possível remover vídeo original após corte: {e}")
        return split_paths

