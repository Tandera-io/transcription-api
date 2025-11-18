from functools import wraps
from fastapi import HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import jwt
import os
import httpx
import logging
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Configuração do Supabase (fallback para tenant padrão)
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET')

SERVICE_API_KEY = os.getenv('TRANSCRIPTION_SERVICE_API_KEY')

# Cache para JWT secrets por tenant
_jwt_secret_cache = {}

# Inicializar apenas se todas as variáveis estiverem presentes
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("⚠️  Supabase não configurado - algumas variáveis de ambiente estão ausentes")
security = HTTPBearer(auto_error=False)

async def get_tenant_jwt_secret() -> str:
    """
    Obtém o JWT secret do tenant atual ou do fallback.
    Suporta multi-tenancy: busca o JWT secret do tenant ativo primeiro.
    """
    # Tentar obter JWT secret do contexto do tenant (multi-tenancy)
    try:
        from middleware.tenant import get_tenant_context
        tenant_ctx = get_tenant_context()
        
        if tenant_ctx.tenant_slug and tenant_ctx.tenant_data:
            # Verificar cache primeiro
            if tenant_ctx.tenant_slug in _jwt_secret_cache:
                logger.debug(f"[Auth] JWT secret do tenant '{tenant_ctx.tenant_slug}' encontrado no cache")
                return _jwt_secret_cache[tenant_ctx.tenant_slug]
            
            # Buscar JWT secret do Registry para este tenant
            registry_url = os.getenv("REGISTRY_API_URL", "http://localhost:3000")
            if registry_url:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        url = f"{registry_url}/api/tenants/by-slug/{tenant_ctx.tenant_slug}/backend-credentials"
                        service_token = os.getenv("REGISTRY_SERVICE_TOKEN")
                        
                        if not service_token:
                            logger.warning("[Auth] REGISTRY_SERVICE_TOKEN não configurado, usando JWT secret padrão")
                        else:
                            headers = {"X-Registry-Service-Token": service_token}
                            response = await client.get(url, headers=headers)
                            
                            if response.status_code == 200:
                                data = response.json()
                                tenant_data = data.get("tenant", {})
                                jwt_secret = tenant_data.get("jwtSecret")
                                
                                if jwt_secret:
                                    _jwt_secret_cache[tenant_ctx.tenant_slug] = jwt_secret
                                    logger.info(f"[Auth] JWT secret do tenant '{tenant_ctx.tenant_slug}' carregado do Registry")
                                    return jwt_secret
                except Exception as e:
                    logger.warning(f"[Auth] Erro ao buscar JWT secret do Registry para tenant '{tenant_ctx.tenant_slug}': {e}")
    except Exception as e:
        logger.debug(f"[Auth] Tenant context não disponível, usando JWT secret padrão: {e}")
    
    # Fallback para JWT secret padrão do .env
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT Secret não configurado")
    
    logger.debug("[Auth] Usando JWT secret padrão do .env")
    return SUPABASE_JWT_SECRET

class AuthMiddleware:
    """Middleware para autenticação usando Supabase Auth"""
    
    @staticmethod
    async def verify_token(token: str) -> dict:
        """Verifica e decodifica o token JWT do Supabase (tenant-aware)"""
        jwt_secret = await get_tenant_jwt_secret()
            
        try:
            # Decodificar o token JWT
            payload = jwt.decode(
                token, 
                jwt_secret, 
                algorithms=["HS256"],
                audience="authenticated"
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expirado")
        except jwt.InvalidTokenError as e:
            logger.warning(f"[Auth] Token inválido: {e}")
            raise HTTPException(status_code=401, detail="Token inválido")
    
    @staticmethod
    def get_user_from_token(payload: dict) -> dict:
        """Extrai informações do usuário do payload do token"""
        return {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "user_metadata": payload.get("user_metadata", {}),
            "app_metadata": payload.get("app_metadata", {})
        }

# Dependência para rotas que requerem autenticação
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependência para obter o usuário atual autenticado"""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token de autenticação necessário")
    
    token = credentials.credentials
    payload = await AuthMiddleware.verify_token(token)
    user = AuthMiddleware.get_user_from_token(payload)
    
    return user

# Dependência opcional (usuário pode ou não estar autenticado)
async def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))):
    """Dependência opcional - retorna usuário se autenticado, None caso contrário"""
    if not credentials:
        return None
    
    try:
        token = credentials.credentials
        jwt_secret = await get_tenant_jwt_secret()
        
        # ✅ Verificar token silenciosamente (sem warnings)
        payload = jwt.decode(
            token, 
            jwt_secret, 
            algorithms=["HS256"],
            audience="authenticated"
        )
        user = AuthMiddleware.get_user_from_token(payload)
        return user
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, HTTPException):
        # ✅ Retornar None silenciosamente para autenticação opcional
        return None

async def get_current_user_or_service(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key")
):
    """
    Dependência que aceita autenticação via JWT (usuários) ou API Key (serviços).
    Útil para endpoints que podem ser chamados tanto por usuários quanto por serviços internos.
    """
    if x_api_key:
        if not SERVICE_API_KEY:
            raise HTTPException(status_code=500, detail="Service API Key não configurado no servidor")
        if x_api_key != SERVICE_API_KEY:
            raise HTTPException(status_code=401, detail="API Key inválida")
        return {
            "id": "service-account",
            "email": "service@internal",
            "role": "service",
            "is_service": True
        }
    
    if not credentials:
        raise HTTPException(status_code=401, detail="Autenticação necessária (JWT ou API Key)")
    
    token = credentials.credentials
    payload = await AuthMiddleware.verify_token(token)
    user = AuthMiddleware.get_user_from_token(payload)
    user["is_service"] = False
    
    return user

# Decorador para funções que requerem autenticação
def require_auth(func):
    """Decorador para rotas que requerem autenticação"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Verifica se current_user está nos kwargs
        if 'current_user' not in kwargs:
            raise HTTPException(status_code=401, detail="Autenticação necessária")
        
        user = kwargs['current_user']
        if not user:
            raise HTTPException(status_code=401, detail="Usuário não autenticado")
        
        return await func(*args, **kwargs)
    return wrapper

# Decorador para verificar permissões específicas
def require_role(required_role: str):
    """Decorador para verificar se o usuário tem uma role específica"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user = kwargs.get('current_user')
            if not user:
                raise HTTPException(status_code=401, detail="Autenticação necessária")
            
            user_role = user.get('role', 'user')
            if user_role != required_role and user_role != 'admin':
                raise HTTPException(status_code=403, detail=f"Permissão '{required_role}' necessária")
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Função para verificar se o usuário é dono do recurso ou admin
def is_owner_or_admin(user: dict, resource_user_id: str) -> bool:
    """Verifica se o usuário é dono do recurso ou admin"""
    return user.get('id') == resource_user_id or user.get('role') == 'admin'

