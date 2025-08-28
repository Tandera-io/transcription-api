from supabase import create_client, Client
from typing import Optional, Dict, Any, List
import os
import logging
from datetime import datetime
import json

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseManager:
    """Gerenciador de banco de dados com funcionalidades avançadas"""
    
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_KEY")
        
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL e SUPABASE_KEY devem estar configurados")
        
        self.supabase: Client = create_client(self.url, self.key)
        logger.info("Conexão com Supabase estabelecida")
    
    def save_transcription(self, job_id: str, video_url: str, transcription_text: str, 
                          metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Salva uma transcrição com metadados opcionais
        
        Args:
            job_id: ID único do job
            video_url: URL do vídeo original
            transcription_text: Texto da transcrição
            metadata: Metadados adicionais (duração, formato, etc.)
        
        Returns:
            Resultado da operação
        """
        try:
            data = {
                "job_id": job_id,
                "video_url": video_url,
                "transcription": transcription_text,
                "created_at": datetime.utcnow().isoformat(),
                "status": "completed",
                "metadata": metadata or {}
            }
            
            result = self.supabase.table("transcriptions").insert(data).execute()
            logger.info(f"Transcrição salva com sucesso: {job_id}")
            return {"success": True, "data": result.data}
            
        except Exception as e:
            error_msg = f"Erro ao salvar transcrição: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

def insert_transcription(data: Dict[str, Any]):
    """Insere uma nova transcrição"""
    db = DatabaseManager()
    return db.supabase.table("transcriptions").insert(data).execute()

def update_transcription(transcription_id: int, data: Dict[str, Any]):
    """Atualiza uma transcrição existente"""
    db = DatabaseManager()
    return db.supabase.table("transcriptions").update(data).eq("id", transcription_id).execute()

def get_transcription(transcription_id: int):
    """Recupera uma transcrição por ID"""
    db = DatabaseManager()
    return db.supabase.table("transcriptions").select("*").eq("id", transcription_id).execute()

def get_user_transcriptions(user_id: str):
    """Recupera todas as transcrições de um usuário"""
    db = DatabaseManager()
    return db.supabase.table("transcriptions").select("*").eq("user_id", user_id).execute()

def delete_transcription(transcription_id: int):
    """Remove uma transcrição"""
    db = DatabaseManager()
    return db.supabase.table("transcriptions").delete().eq("id", transcription_id).execute()

