import os
import logging
from urllib.parse import urlparse
from supabase import create_client, Client
from typing import Optional

logger = logging.getLogger(__name__)


def _clean_env_value(v: str) -> str:
    return (v or "").strip().strip('"').strip("'")


def get_supabase_client() -> Client:
    """Cria cliente Supabase validando URL/chave e registrando host alvo.
    
    Suporta multi-tenancy: tenta obter credenciais do tenant atual primeiro,
    depois faz fallback para variáveis de ambiente.
    """
    # Tentar obter credenciais do contexto do tenant (multi-tenancy)
    try:
        from middleware.tenant import get_tenant_context
        tenant_ctx = get_tenant_context()
        if tenant_ctx.tenant_slug and tenant_ctx.tenant_data:
            url = tenant_ctx.get_supabase_url()
            key = tenant_ctx.get_anon_key()
            
            if url and key:
                logger.info(f"[Supabase] Usando credenciais do tenant: {tenant_ctx.tenant_slug}")
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    return create_client(url, key)
                else:
                    logger.warning(f"[Supabase] URL do tenant inválida: {url}, usando fallback")
    except Exception as e:
        logger.debug(f"[Supabase] Tenant context não disponível, usando credenciais padrão: {e}")
    
    # Fallback para credenciais padrão do .env
    url = _clean_env_value(os.getenv("SUPABASE_URL") or "")
    key = _clean_env_value(
        os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    )
    
    if not url:
        raise RuntimeError("SUPABASE_URL não configurado")
    
    if not key:
        raise RuntimeError("Chave Supabase ausente (SUPABASE_KEY ou SUPABASE_SERVICE_KEY)")
    
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(f"SUPABASE_URL inválido: '{url}'. Esperado algo como https://<project>.supabase.co")
    
    try:
        logger.info(f"[Supabase] Usando host={parsed.hostname} scheme={parsed.scheme} (fallback)")
    except Exception:
        pass
    
    return create_client(url, key)


def insert_transcription(data):
    """Insere uma nova transcrição no Supabase"""
    supabase = get_supabase_client()
    
    # Se não tiver user_id, deixar como None (NULL no banco)
    if 'user_id' not in data:
        data['user_id'] = None
    
    result = supabase.table("transcriptions").insert(data).execute()
    return result


def update_transcription(transcription_id, data):
    """Atualiza uma transcrição existente"""
    supabase = get_supabase_client()
    result = supabase.table("transcriptions").update(data).eq("id", transcription_id).execute()
    return result


def get_transcription(transcription_id):
    """Busca uma transcrição por ID"""
    supabase = get_supabase_client()
    result = supabase.table("transcriptions").select("*").eq("id", transcription_id).execute()
    return result


def get_transcriptions_by_user(user_id):
    """Busca transcrições de um usuário específico"""
    supabase = get_supabase_client()
    result = supabase.table("transcriptions").select("*").eq("user_id", user_id).execute()
    return result

