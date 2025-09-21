
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
        
        # Log para debug
        logger.info(f"AssemblyAI API Key encontrada: {'SIM' if self.api_key else 'NÃO'}")
        if self.api_key:
            logger.info(f"API Key (primeiros 10 chars): {self.api_key[:10]}...")
        
        if not self.api_key:
            logger.error("❌ ASSEMBLYAI_API_KEY não encontrada em nenhuma variável de ambiente")
            logger.error("Variáveis de ambiente disponíveis:")
            for key, value in os.environ.items():
                if 'ASSEMBLY' in key.upper() or 'API' in key.upper():
                    logger.error(f"  {key}: {'***' if value else 'None'}")
            raise ValueError("ASSEMBLYAI_API_KEY deve estar configurado")
        
        self.headers = {"authorization": self.api_key}
        self.base_url = "https://api.assemblyai.com/v2"
        
        logger.info("✅ Serviço AssemblyAI inicializado com sucesso")
    
    def upload_file(self, file_path: str) -> Dict[str, Any]:
        """
        Upload de arquivo para AssemblyAI com validação
        
        Args:
            file_path: Caminho para o arquivo de áudio
        
        Returns:
            Resultado do upload com URL ou erro
        """
        try:
            # Validar se o arquivo existe
            if not os.path.exists(file_path):
                return {"success": False, "error": f"Arquivo não encontrado: {file_path}"}
            
            # Verificar tamanho do arquivo (limite de 5GB)
            file_size = os.path.getsize(file_path)
            if file_size > 5 * 1024 * 1024 * 1024:  # 5GB
                return {"success": False, "error": "Arquivo muito grande (máximo 5GB)"}
            
            logger.info(f"Iniciando upload do arquivo: {file_path} ({file_size} bytes)")
            logger.debug(f"Arquivo encontrado e pronto para upload: {file_path}")
            
            # Enviar RAW bytes conforme a documentação da AssemblyAI
            # Importante: NÃO usar multipart (files={"file": f}), pois isso corrompe o payload
            # e resulta em "Transcoding failed. File does not appear to contain audio."
            with open(file_path, "rb") as f:
                response = requests.post(
                    f"{self.base_url}/upload",
                    headers={**self.headers, "Content-Type": "application/octet-stream"},
                    data=f,
                    timeout=300  # 5 minutos timeout
                )
            
            response.raise_for_status()
            upload_url = response.json()["upload_url"]
            logger.debug(f"Resposta completa do upload: {response.json()}")
            
            logger.info(f"Upload concluído com sucesso: {upload_url}")

            # Teste de integridade: baixar o arquivo da URL e comparar hash
            import hashlib
            def sha256sum(path):
                h = hashlib.sha256()
                with open(path, 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        h.update(chunk)
                return h.hexdigest()
            original_hash = sha256sum(file_path)
            try:
                # Corrigir problema SSL/certificado com verificação desabilitada temporariamente
                r = requests.get(upload_url, timeout=60, verify=False)
                r.raise_for_status()
                temp_download = file_path + ".downloaded"
                with open(temp_download, 'wb') as out:
                    out.write(r.content)
                downloaded_hash = sha256sum(temp_download)
                os.remove(temp_download)
                if original_hash == downloaded_hash:
                    logger.info(f"[INTEGRIDADE] Hash SHA256 confere: {original_hash}")
                else:
                    logger.error(f"[INTEGRIDADE] Hash SHA256 DIFERENTE! Original: {original_hash} | Baixado: {downloaded_hash}")
            except Exception as e:
                logger.warning(f"[INTEGRIDADE] Não foi possível verificar integridade do arquivo - continuando: {e}")

            return {"success": True, "upload_url": upload_url}
            
        except requests.exceptions.Timeout:
            error_msg = "Timeout durante upload do arquivo"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        except requests.exceptions.RequestException as e:
            error_msg = f"Erro na requisição de upload: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        except Exception as e:
            error_msg = f"Erro inesperado durante upload: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
    
    def start_transcription(self, audio_url: str, 
                          config: Optional[TranscriptionConfig] = None) -> Dict[str, Any]:
        """
        Inicia processo de transcrição com configurações avançadas
        
        Args:
            audio_url: URL do áudio (upload_url do AssemblyAI)
            config: Configurações de transcrição
        
        Returns:
            Resultado com transcript_id ou erro
        """
        try:
            if not config:
                config = TranscriptionConfig()
            
            # Enviar apenas os campos obrigatórios para AssemblyAI
            json_data = {
                "audio_url": audio_url,
                "language_code": config.language_code
            }
            
            # Remover campos com valor None (que podem causar erro na API)
            json_data = {k: v for k, v in json_data.items() if v is not None}
            
            # Adicionar configurações opcionais
            if config.webhook_url:
                json_data["webhook_url"] = config.webhook_url
            
            if config.word_boost:
                json_data["word_boost"] = config.word_boost
                json_data["boost_param"] = config.boost_param
            
            logger.info(f"Iniciando transcrição com configurações: {json_data}")
            logger.debug(f"Payload enviado para AssemblyAI:\n{json.dumps(json_data, indent=2)}")
            
            response = requests.post(
                f"{self.base_url}/transcript",
                json=json_data,
                headers=self.headers,
                timeout=30
            )
            
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as http_err:
                # Logar o corpo da resposta de erro para facilitar diagnóstico
                logger.error(f"Erro ao iniciar transcrição: {http_err}. Resposta AssemblyAI: {response.text}")
                return {"success": False, "error": f"Erro ao iniciar transcrição: {http_err}. Resposta AssemblyAI: {response.text}"}
            result = response.json()
            transcript_id = result["id"]
            
            logger.info(f"Transcrição iniciada com ID: {transcript_id}")
            return {"success": True, "transcript_id": transcript_id}
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Erro ao iniciar transcrição: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        except Exception as e:
            error_msg = f"Erro inesperado ao iniciar transcrição: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
    
    def get_transcription_status(self, transcript_id: str) -> Dict[str, Any]:
        """
        Verifica o status de uma transcrição
        
        Args:
            transcript_id: ID da transcrição
        
        Returns:
            Status e dados da transcrição
        """
        try:
            response = requests.get(
                f"{self.base_url}/transcript/{transcript_id}",
                headers=self.headers,
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            return {
                "success": True,
                "status": result["status"],
                "data": result
            }
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Erro ao verificar status: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
    
    def wait_for_completion(self, transcript_id: str, 
                          max_wait_time: int = 3600) -> Dict[str, Any]:
        """
        Aguarda conclusão da transcrição com timeout
        
        Args:
            transcript_id: ID da transcrição
            max_wait_time: Tempo máximo de espera em segundos (padrão: 1 hora)
        
        Returns:
            Resultado final da transcrição
        """
        start_time = time.time()
        polling_interval = 5  # Começar com 5 segundos
        max_interval = 30     # Máximo de 30 segundos
        
        logger.info(f"Aguardando conclusão da transcrição: {transcript_id}")
        
        while time.time() - start_time < max_wait_time:
            status_result = self.get_transcription_status(transcript_id)
            
            if not status_result["success"]:
                return status_result
            
            status = status_result["status"]
            
            if status == "completed":
                logger.info(f"Transcrição concluída: {transcript_id}")
                return {
                    "success": True,
                    "status": "completed",
                    "data": status_result["data"]
                }
            
            elif status == "error":
                error_msg = status_result["data"].get("error", "Erro desconhecido na transcrição")
                logger.error(f"Erro na transcrição {transcript_id}: {error_msg}")
                return {
                    "success": False,
                    "status": "error",
                    "error": error_msg
                }
            
            # Aguardar antes da próxima verificação
            time.sleep(polling_interval)
            
            # Aumentar intervalo gradualmente para reduzir carga na API
            polling_interval = min(polling_interval + 2, max_interval)
        
        # Timeout atingido
        error_msg = f"Timeout aguardando transcrição {transcript_id} após {max_wait_time} segundos"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    def get_transcription_text(self, transcript_id: str) -> Dict[str, Any]:
        """
        Recupera apenas o texto da transcrição
        
        Args:
            transcript_id: ID da transcrição
        
        Returns:
            Texto da transcrição ou erro
        """
        result = self.get_transcription_status(transcript_id)
        
        if not result["success"]:
            return result
        
        if result["status"] != "completed":
            return {
                "success": False,
                "error": f"Transcrição não concluída. Status: {result['status']}"
            }
        
        text = result["data"].get("text", "")
        return {"success": True, "text": text}
    
    def get_detailed_transcription(self, transcript_id: str) -> Dict[str, Any]:
        """
        Recupera transcrição detalhada com speakers e timestamps
        
        Args:
            transcript_id: ID da transcrição
        
        Returns:
            Dados detalhados da transcrição
        """
        result = self.get_transcription_status(transcript_id)
        
        if not result["success"]:
            return result
        
        if result["status"] != "completed":
            return {
                "success": False,
                "error": f"Transcrição não concluída. Status: {result['status']}"
            }
        
        data = result["data"]
        
        # Extrair informações detalhadas
        detailed_result = {
            "success": True,
            "text": data.get("text", ""),
            "confidence": data.get("confidence", 0),
            "audio_duration": data.get("audio_duration", 0),
            "speakers": [],
            "utterances": data.get("utterances", []),
            "words": data.get("words", [])
        }
        
        # Processar speakers se disponível
        if "speaker_labels" in data and data["speaker_labels"]:
            speakers = set()
            for utterance in data.get("utterances", []):
                if "speaker" in utterance:
                    speakers.add(utterance["speaker"])
            detailed_result["speakers"] = list(speakers)
        
        return detailed_result
    
    def process_complete_transcription(self, file_path: str, 
                                     config: Optional[TranscriptionConfig] = None) -> Dict[str, Any]:
        """
        Processo completo: upload + transcrição + aguardar resultado
        
        Args:
            file_path: Caminho para o arquivo de áudio
            config: Configurações de transcrição
        
        Returns:
            Resultado completo da transcrição
        """
        logger.info(f"Iniciando processo completo de transcrição: {file_path}")
        
        # 1. Upload do arquivo
        upload_result = self.upload_file(file_path)
        if not upload_result["success"]:
            return upload_result
        
        # 2. Iniciar transcrição
        transcription_result = self.start_transcription(
            upload_result["upload_url"], config
        )
        if not transcription_result["success"]:
            return transcription_result
        
        # 3. Aguardar conclusão
        final_result = self.wait_for_completion(
            transcription_result["transcript_id"]
        )
        
        if final_result["success"]:
            # Adicionar ID da transcrição ao resultado
            final_result["transcript_id"] = transcription_result["transcript_id"]
        
        return final_result


# Função alternativa direta para transcrição via requests.post
def transcribe_audio_with_requests(audio_url: str) -> Dict[str, Any]:
    """
    Alternativa direta para transcrever áudio usando requests e payload customizado
    """
    url = "https://api.assemblyai.com/v2/transcript"
    headers = {
        "authorization": os.getenv("ASSEMBLYAI_API_KEY"),
        "content-type": "application/json"
    }
    data = {
        "audio_url": audio_url,
        "language_code": "pt",
        "speaker_labels": True,
        "auto_punctuation": True,
        "format_text": True,
        "dual_channel": False
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


