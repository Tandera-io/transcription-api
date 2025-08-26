import requests
import time
import os
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import json

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class TranscriptionConfig:
    """Configuração para transcrição"""
    language_code: str = "pt"  # Português
    speaker_labels: bool = True  # Identificação de speakers
    auto_punctuation: bool = True  # Pontuação automática
    format_text: bool = True  # Formatação de texto
    dual_channel: bool = False  # Áudio dual channel
    webhook_url: Optional[str] = None  # URL para webhook
    word_boost: Optional[List[str]] = None  # Palavras para boost
    boost_param: str = "default"  # Parâmetro de boost

class AssemblyAIService:
    """Serviço melhorado para integração com AssemblyAI"""
    
    def __init__(self):
        # Tentar múltiplas formas de obter a API key
        self.api_key = None
        
        # 1. Tentar variável de ambiente padrão
        self.api_key = os.getenv("ASSEMBLYAI_API_KEY")
        
        # 2. Tentar variável de ambiente alternativa (Railway às vezes usa nomes diferentes)
        if not self.api_key:
            self.api_key = os.getenv("ASSEMBLY_AI_API_KEY")
        
        # 3. Tentar variável de ambiente do Railway
        if not self.api_key:
            self.api_key = os.getenv("RAILWAY_ASSEMBLYAI_API_KEY")
        
        # 4. Tentar variável de ambiente genérica
        if not self.api_key:
            self.api_key = os.getenv("API_KEY")
        
        # 5. Se ainda não encontrou, tentar carregar de arquivo .env
        if not self.api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                self.api_key = os.getenv("ASSEMBLYAI_API_KEY")
            except ImportError:
                pass
        
        logger.info(f"AssemblyAI API Key encontrada: {'SIM' if self.api_key else 'NÃO'}")
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY deve estar configurado")
        
        self.headers = {"authorization": self.api_key}
        self.base_url = "https://api.assemblyai.com/v2"
        
        logger.info("✅ Serviço AssemblyAI inicializado com sucesso")
    
    def upload_file(self, file_path: str) -> Dict[str, Any]:
        try:
            if not os.path.exists(file_path):
                return {"success": False, "error": f"Arquivo não encontrado: {file_path}"}
            file_size = os.path.getsize(file_path)
            if file_size > 5 * 1024 * 1024 * 1024:
                return {"success": False, "error": "Arquivo muito grande (máximo 5GB)"}
            logger.info(f"Iniciando upload do arquivo: {file_path} ({file_size} bytes)")
            with open(file_path, "rb") as f:
                response = requests.post(
                    f"{self.base_url}/upload",
                    headers=self.headers,
                    files={"file": f},
                    timeout=300
                )
            response.raise_for_status()
            upload_url = response.json()["upload_url"]
            logger.info(f"Upload concluído: {upload_url}")
            return {"success": True, "upload_url": upload_url}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Erro na requisição de upload: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Erro inesperado durante upload: {str(e)}"}
    
    def start_transcription(self, audio_url: str, 
                          config: Optional[TranscriptionConfig] = None) -> Dict[str, Any]:
        try:
            if not config:
                config = TranscriptionConfig()
            json_data = {
                "audio_url": audio_url,
                "language_code": config.language_code
            }
            if config.webhook_url:
                json_data["webhook_url"] = config.webhook_url
            if config.word_boost:
                json_data["word_boost"] = config.word_boost
                json_data["boost_param"] = config.boost_param
            logger.info(f"Iniciando transcrição com configurações: {json_data}")
            response = requests.post(
                f"{self.base_url}/transcript",
                json=json_data,
                headers=self.headers,
                timeout=30
            )
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as http_err:
                logger.error(f"Erro ao iniciar transcrição: {http_err}. Resposta: {response.text}")
                return {"success": False, "error": f"Erro ao iniciar transcrição: {http_err}. Resposta AssemblyAI: {response.text}"}
            result = response.json()
            return {"success": True, "transcript_id": result["id"]}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Erro ao iniciar transcrição: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Erro inesperado ao iniciar transcrição: {str(e)}"}
    
    def get_transcription_status(self, transcript_id: str) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.base_url}/transcript/{transcript_id}",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            return {"success": True, "status": result["status"], "data": result}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Erro ao verificar status: {str(e)}"}
    
    def wait_for_completion(self, transcript_id: str, max_wait_time: int = 3600) -> Dict[str, Any]:
        start_time = time.time()
        polling_interval = 5
        max_interval = 30
        logger.info(f"Aguardando conclusão da transcrição: {transcript_id}")
        while time.time() - start_time < max_wait_time:
            status_result = self.get_transcription_status(transcript_id)
            if not status_result["success"]:
                return status_result
            status = status_result["status"]
            if status == "completed":
                logger.info(f"Transcrição concluída: {transcript_id}")
                return {"success": True, "status": "completed", "data": status_result["data"]}
            elif status == "error":
                error_msg = status_result["data"].get("error", "Erro desconhecido na transcrição")
                return {"success": False, "status": "error", "error": error_msg}
            time.sleep(polling_interval)
            polling_interval = min(polling_interval + 2, max_interval)
        return {"success": False, "error": f"Timeout aguardando transcrição {transcript_id} após {max_wait_time} segundos"}


