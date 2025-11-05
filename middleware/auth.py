from functools import wraps
from fastapi import HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import jwt
import os
from supabase import create_client, Client

# Configuração do Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET')

SERVICE_API_KEY = os.getenv('TRANSCRIPTION_SERVICE_API_KEY')

# Inicializar apenas se todas as variáveis estiverem presentes
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("⚠️  Supabase não configurado - algumas variáveis de ambiente estão ausentes")
security = HTTPBearer(auto_error=False)

class AuthMiddleware:
    """Middleware para autenticação usando Supabase Auth"""
    
    @staticmethod
    def verify_token(token: str) -> dict:
        """Verifica e decodifica o token JWT do Supabase"""
        if not SUPABASE_JWT_SECRET:
            raise HTTPException(status_code=500, detail="JWT Secret não configurado")
            
        try:
            # Decodificar o token JWT
            payload = jwt.decode(
                token, 
                SUPABASE_JWT_SECRET, 
                algorithms=["HS256"],
                audience="authenticated"
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expirado")
        except jwt.InvalidTokenError:
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
    payload = AuthMiddleware.verify_token(token)
    user = AuthMiddleware.get_user_from_token(payload)
    
    return user

# Dependência opcional (usuário pode ou não estar autenticado)
async def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))):
    """Dependência opcional - retorna usuário se autenticado, None caso contrário"""
    if not credentials:
        return None
    
    try:
        token = credentials.credentials
        payload = AuthMiddleware.verify_token(token)
        user = AuthMiddleware.get_user_from_token(payload)
        return user
    except HTTPException:
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
    payload = AuthMiddleware.verify_token(token)
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

