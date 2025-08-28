from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import os
from typing import Optional, Dict, Any

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[Dict[str, Any]]:
    """
    Extrai e valida o usuário atual do token JWT
    
    Args:
        credentials: Credenciais de autorização do header
    
    Returns:
        Dados do usuário ou None se inválido
    """
    try:
        token = credentials.credentials
        jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
        
        if not jwt_secret:
            raise HTTPException(status_code=500, detail="JWT secret não configurado")
        
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        
        user_data = {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role", "user")
        }
        
        return user_data
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Erro de autenticação: {str(e)}")

