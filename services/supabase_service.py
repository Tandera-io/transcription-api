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
                logger.info(f"[Supabase] Usando credenciais do tenant: {tenant_ctx.tenant_slug} | URL: {url[:50]}...")
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


def get_supabase_service_client() -> Client:
    """
    Cria cliente Supabase com SERVICE_ROLE (bypass RLS).
    
    Necessário para operações administrativas e bypass de RLS.
    O middleware deve ter buscado serviceRole do Registry previamente.
    """
    # Tentar obter service_key do contexto do tenant (multi-tenancy)
    try:
        from middleware.tenant import get_tenant_context
        
        tenant_ctx = get_tenant_context()
        
        if tenant_ctx.tenant_slug and tenant_ctx.tenant_data:
            # Verificar se tem serviceRole no contexto
            service_key = tenant_ctx.get_service_key()
            
            if service_key:
                url = tenant_ctx.get_supabase_url()
                if url:
                    logger.info(f"[Supabase] Usando SERVICE_ROLE do tenant: {tenant_ctx.tenant_slug}")
                    parsed = urlparse(url)
                    if parsed.scheme and parsed.netloc:
                        return create_client(url, service_key)
                    else:
                        logger.warning(f"[Supabase] URL do tenant inválida: {url}, usando fallback")
            else:
                logger.warning(f"[Supabase] serviceRole não disponível para tenant: {tenant_ctx.tenant_slug}, usando fallback")
    
    except Exception as e:
        logger.warning(f"[Supabase] Erro ao obter SERVICE_ROLE do tenant, usando fallback: {e}")
    
    # Fallback para credenciais padrão do .env
    url = _clean_env_value(os.getenv("SUPABASE_URL") or "")
    service_key = _clean_env_value(os.getenv("SUPABASE_SERVICE_KEY") or "")
    
    if not url or not service_key:
        raise ValueError("SUPABASE_URL e SUPABASE_SERVICE_KEY devem estar configurados no .env")
    
    logger.info("[Supabase] Usando SERVICE_ROLE do .env (fallback)")
    return create_client(url, service_key)


# Alias para compatibilidade
get_supabase_admin = get_supabase_service_client


def insert_transcription(data, supabase_url: str = None, service_key: str = None):
    """Insere uma nova transcrição no Supabase
    
    Usa SERVICE_ROLE para bypass RLS, pois é uma operação de sistema.
    
    Args:
        data: Dados da transcrição
        supabase_url: URL do Supabase (opcional, para background tasks)
        service_key: Service role key (opcional, para background tasks)
    """
    # Se credenciais explícitas foram passadas, usar elas (para background tasks)
    if supabase_url and service_key:
        logger.info(f"[Supabase] Usando credenciais explícitas para insert | URL: {supabase_url[:50]}...")
        supabase = create_client(supabase_url, service_key)
    else:
        # Caso contrário, usar contexto (requisições HTTP normais)
        supabase = get_supabase_service_client()
    
    # Se não tiver user_id, deixar como None (NULL no banco)
    if 'user_id' not in data:
        data['user_id'] = None
    
    result = supabase.table("transcriptions").insert(data).execute()
    return result


def update_transcription(transcription_id, data, supabase_url: str = None, service_key: str = None):
    """Atualiza uma transcrição existente
    
    Usa SERVICE_ROLE para bypass RLS, pois é uma operação de sistema.
    
    Args:
        transcription_id: ID da transcrição
        data: Dados a atualizar
        supabase_url: URL do Supabase (opcional, para background tasks)
        service_key: Service role key (opcional, para background tasks)
    """
    try:
        # Se credenciais explícitas foram passadas, usar elas (para background tasks)
        if supabase_url and service_key:
            logger.info(f"[Supabase] Usando credenciais explícitas para update | URL: {supabase_url[:50]}...")
            supabase = create_client(supabase_url, service_key)
        else:
            supabase = get_supabase_service_client()
        print(f"[SUPABASE] Atualizando transcription_id={transcription_id}")
        print(f"[SUPABASE] Campos a atualizar: {list(data.keys())}")
        result = supabase.table("transcriptions").update(data).eq("id", transcription_id).execute()
        print(f"[SUPABASE] Atualização bem-sucedida: {len(result.data)} registro(s) atualizado(s)")
        return result
    except Exception as e:
        print(f"[SUPABASE] ❌ Erro ao atualizar transcrição {transcription_id}: {e}")
        import traceback
        traceback.print_exc()
        raise


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

