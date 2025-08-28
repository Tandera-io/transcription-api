# 🗑️ CHECKLIST DE LIMPEZA - BACKEND ANTIGO

## ⚠️ **IMPORTANTE: VALIDAR ANTES DE REMOVER**

Antes de remover qualquer arquivo, **CONFIRME** que o microserviço está funcionando 100% em produção.

---

## 📂 **ARQUIVOS PARA REMOVER COMPLETAMENTE**

### **Serviços Migrados**
```bash
# Localização: transcription-app/backend/services/
❌ assembly_service.py          # 436 linhas - Migrado para microserviço
❌ nlp_service.py              # 396 linhas - Migrado para microserviço  
❌ openai_service.py           # Funções GPT - Migrado para microserviço
```

### **Tasks de Transcrição**
```bash
# Localização: transcription-app/backend/tasks/
❌ transcription_tasks.py      # Tasks Celery - Migrado para microserviço
```

---

## 🔧 **CÓDIGO PARA REMOVER DO main.py**

### **Classes de Modelo (Migradas)**
```python
# Em transcription-app/backend/api/main.py - REMOVER:

class TranscriptionRequest(BaseModel):
    video_url: str
    title: Optional[str] = None
    meeting_type: Optional[str] = None
    participants: Optional[List[str]] = None

class TranscriptionResponse(BaseModel):
    job_id: str
    message: str
    status: str
```

### **Endpoints de Transcrição (Migrados)**
```python
# Em transcription-app/backend/api/main.py - REMOVER:

@app.post("/api/transcribe", response_model=TranscriptionResponse)
async def transcribe_from_url(req: TranscriptionRequest, current_user: dict = Depends(get_current_user)):
    # TODO: REMOVER TODA ESTA FUNÇÃO - 50+ linhas

@app.post("/api/transcribe/upload", response_model=TranscriptionResponse)  
async def transcribe_upload(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    # TODO: REMOVER TODA ESTA FUNÇÃO - 50+ linhas
```

### **Funções de Processamento (Migradas)**
```python
# Em transcription-app/backend/api/main.py - REMOVER:

def process_and_save_transcription(text: str, job_id: str, video_url: str, file_name: str, user_id: str):
    # TODO: REMOVER TODA ESTA FUNÇÃO - 100+ linhas

def _extract_json_from_text(text: str) -> dict:
    # TODO: REMOVER TODA ESTA FUNÇÃO - 20+ linhas
```

---

## 📦 **DEPENDÊNCIAS PARA REMOVER**

### **requirements.txt - Verificar e Remover**
```python
# Em transcription-app/backend/requirements.txt - REMOVER se não usadas:

assemblyai>=0.33.0             # ❓ Verificar se usado em outros lugares
ffmpeg-python==0.2.0           # ❓ Verificar se usado em outros lugares  
pydub==0.25.1                  # ❓ Verificar se usado em outros lugares
moviepy==1.0.3                 # ❓ Verificar se usado em outros lugares
```

### **Imports para Remover**
```python
# Em transcription-app/backend/api/main.py - REMOVER imports:

from services.assembly_service import AssemblyAIService
from services.nlp_service import NLPService  
from services.openai_service import gpt_4_completion
# Outros imports relacionados a transcrição
```

---

## 🔒 **ARQUIVOS PARA MANTER (NÃO REMOVER)**

### **Manter Intactos**
```bash
✅ backend/models/database.py           # Usado por outros módulos
✅ backend/api/main.py                  # Endpoints principais (exceto transcrição)
✅ backend/services/                    # Outros serviços não migrados
✅ backend/middleware/                  # Middleware de auth, etc.
✅ frontend/                           # Frontend React
✅ requirements.txt                     # Dependências principais (limpar apenas específicas)
```

---

## 🔄 **IMPLEMENTAÇÃO DE API GATEWAY (RECOMENDADO)**

### **Adicionar Proxy no Backend Principal**
```python
# Em transcription-app/backend/api/main.py - ADICIONAR:

import httpx
from fastapi import HTTPException

TRANSCRIPTION_SERVICE_URL = "https://liacrm-transcription-api.up.railway.app"

@app.post("/api/transcribe")
async def transcribe_proxy(req: dict):
    """Proxy para microserviço de transcrição via URL"""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{TRANSCRIPTION_SERVICE_URL}/api/transcribe",
                json=req,
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(status_code=response.status_code, detail=response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no microserviço: {str(e)}")

@app.post("/api/transcribe/upload")
async def transcribe_upload_proxy(file: UploadFile = File(...)):
    """Proxy para microserviço de upload"""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            files = {"file": (file.filename, await file.read(), file.content_type)}
            response = await client.post(
                f"{TRANSCRIPTION_SERVICE_URL}/api/transcribe/upload",
                files=files
            )
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(status_code=response.status_code, detail=response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no microserviço: {str(e)}")
```

### **Adicionar Dependência**
```python
# Em transcription-app/backend/requirements.txt - ADICIONAR:
httpx>=0.24.0                          # Para proxy HTTP
```

---

## ✅ **CHECKLIST DE VALIDAÇÃO**

### **Antes da Limpeza**
- [ ] Microserviço funcionando em produção
- [ ] Testes de transcrição via URL passando
- [ ] Testes de upload passando  
- [ ] Power Automate funcionando sem erros
- [ ] Dados sendo salvos corretamente no Supabase

### **Durante a Limpeza**
- [ ] Backup do código antes de remover
- [ ] Remover arquivos um por vez
- [ ] Testar após cada remoção
- [ ] Verificar se build ainda funciona

### **Após a Limpeza**
- [ ] Backend principal ainda funciona
- [ ] Frontend ainda acessa APIs
- [ ] Nenhuma funcionalidade quebrada
- [ ] Logs sem erros de import

---

## 🚨 **PLANO DE ROLLBACK**

### **Se Algo Der Errado**
1. **Parar remoções** imediatamente
2. **Restaurar backup** do código
3. **Verificar funcionamento** do sistema
4. **Investigar problema** antes de continuar

### **Rollback Completo**
```bash
# Se necessário, pode desativar microserviço e voltar ao código antigo:
git checkout HEAD~1  # Voltar commit anterior
# Reativar endpoints antigos no backend principal
```

---

## 📊 **ESTIMATIVA DE LIMPEZA**

### **Linhas de Código Removidas**
- **assembly_service.py**: ~436 linhas
- **nlp_service.py**: ~396 linhas  
- **openai_service.py**: ~100 linhas
- **transcription_tasks.py**: ~200 linhas
- **Endpoints main.py**: ~200 linhas
- **Total**: ~1.332 linhas removidas

### **Benefícios**
- ✅ **Código mais limpo** - Menos complexidade
- ✅ **Deploy mais rápido** - Menos dependências
- ✅ **Manutenção mais fácil** - Responsabilidades claras
- ✅ **Menos bugs** - Menos código = menos problemas

---

## 📞 **SUPORTE**

### **Em Caso de Dúvidas**
- **Microserviço**: https://liacrm-transcription-api.up.railway.app/docs
- **PR Original**: https://github.com/Tandera-io/transcription-api/pull/1
- **Contato**: jairo.soares@advicegrowth.com.br

---

*Checklist criado em: 28 de Agosto de 2025*  
*Versão: 1.0*  
*Status: Pronto para execução*
