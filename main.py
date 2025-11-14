from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Depends, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import os
import uuid
import json
import hashlib
import threading

from supabase import create_client, Client

from services.assembly_service import AssemblyAIService
from services.download_service import DownloadService
from services.supabase_service import insert_transcription, update_transcription
from services.openai_service import gpt_4_completion
from middleware.auth import get_current_user, get_current_user_or_service, get_current_user_optional


class TranscriptionRequest(BaseModel):
    video_url: str
    title: Optional[str] = None
    meeting_type: Optional[str] = None
    participants: Optional[List[str]] = None


class TranscriptionResponse(BaseModel):
    job_id: str
    message: str
    status: str


app = FastAPI(title="Tandera Transcription API", version="1.0.0")

cors_env = os.getenv("CORS_ORIGINS", "").strip()
if cors_env:
    allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
else:
    allowed_origins = [
        "*"
    ]

# Adicionar middleware de tenant PRIMEIRO (será executado por último devido à ordem inversa do FastAPI)
from middleware.tenant import TenantMiddleware
app.add_middleware(TenantMiddleware)

# Adicionar CORS por último (será executado primeiro, processando OPTIONS antes do tenant)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https://.*\.(netlify\.app|liacrm\.io)$",  # permite deploy previews do Netlify e subdomínios liacrm.io
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


"""Idempotência e cliente Supabase"""
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
_supabase_client: Optional[Client] = None
try:
    if SUPABASE_URL and SUPABASE_KEY:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception:
    _supabase_client = None

_locks_guard = threading.Lock()
_hash_locks: Dict[str, threading.Lock] = {}


def _get_lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _hash_locks.get(key)
        if not lock:
            lock = threading.Lock()
            _hash_locks[key] = lock
        return lock


def _sha256_str(value: str) -> str:
    return hashlib.sha256((value or "").strip().encode("utf-8")).hexdigest()


def _find_transcription_by_hash(url_hash: str) -> Optional[Dict[str, Any]]:
    """Busca transcrição por hash usando SERVICE_ROLE para bypass RLS"""
    if not url_hash:
        return None
    try:
        from services.supabase_service import get_supabase_service_client
        supabase = get_supabase_service_client()
        res = (
            supabase
            .table("transcriptions")
            .select("id, job_id, status")
            .eq("url_hash", url_hash)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None)
        if data and len(data) > 0:
            return data[0]
    except Exception as e:
        print(f"[DEBUG] Erro ao buscar transcrição por hash: {e}")
        return None
    return None


def _extract_json_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        import re
        t = (text or "").strip()
        if t.lower().startswith("```json"):
            t = t[len("```json"):].strip()
        if t.lower().startswith("```"):
            t = t[len("```"):].strip()
        if t.endswith("```"):
            t = t[:-3].strip()
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(t[start:end + 1])
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    return {}


def process_and_save_transcription(
    transcription_text: str, 
    job_id: str, 
    video_url: str, 
    file_name: str,
    user_id: Optional[str] = None, 
    url_hash: Optional[str] = None,
    meeting_type: Optional[str] = None,
    include_nlp: bool = True,
    speaker_labels: bool = True,
    supabase_url: Optional[str] = None,
    service_key: Optional[str] = None
):
    # Prompt detalhado (igual ao backend principal)
    prompt = f"""
Analise a seguinte transcrição de reunião de forma MUITO DETALHADA e retorne APENAS um JSON válido em português brasileiro.

TRANSCRIÇÃO:
{transcription_text}

Estrutura obrigatória do JSON:
{{
  "title": "título descritivo e profissional da reunião (baseado no conteúdo)",
  "client": "nome do cliente", 
  "project": "nome do projeto",
  "rito": "tipo de reunião",
  "executive_summary": "resumo executivo detalhado (3-4 frases)",
  "decisions": ["decisão específica com contexto", "outra decisão"],
  "main_points": ["ponto principal com detalhes", "outro ponto"],
  "action_items": ["ação específica com responsável e prazo", "outra ação"],
  "tag": ["tag1", "tag2"],
  "participants": ["nome1", "nome2"],
  "metrics": ["métrica 1 com valor", "métrica 2 com valor"],
  "dates": ["data mencionada 1", "data mencionada 2"],
  "risks": ["risco identificado 1", "risco identificado 2"],
  "next_steps": ["próximo passo 1", "próximo passo 2"]
}}

REGRAS IMPORTANTES:
- Responda SEMPRE em português brasileiro
- Seja MUITO DETALHADO - extraia números, métricas, datas, nomes
- Use "N/A" para campos vazios
- Mantenha JSON válido
- Para o TÍTULO: crie um título profissional e descritivo baseado no CONTEÚDO da reunião
- Inclua contexto específico nas decisões e ações
- Capture métricas mencionadas (porcentagens, valores, etc.)
- Identifique participantes da reunião
- Extraia prazos e datas mencionadas
- Identifique riscos e próximos passos
"""

    try:
        result_text = gpt_4_completion(prompt, max_tokens=2000)
        parsed = _extract_json_from_text(result_text)
    except Exception:
        parsed = {}

    title = parsed.get('title') or f"Reunião - {file_name}"
    client = parsed.get('client') or 'N/A'
    project = parsed.get('project') or 'N/A'
    rito = parsed.get('rito') or 'N/A'
    executive_summary = parsed.get('executive_summary') or 'N/A'
    decisions = parsed.get('decisions') if isinstance(parsed.get('decisions'), list) else []
    main_points = parsed.get('main_points') if isinstance(parsed.get('main_points'), list) else []
    action_items = parsed.get('action_items') if isinstance(parsed.get('action_items'), list) else []
    tags = parsed.get('tag') if isinstance(parsed.get('tag'), list) else []
    participants = parsed.get('participants') if isinstance(parsed.get('participants'), list) else []
    metrics = parsed.get('metrics') if isinstance(parsed.get('metrics'), list) else []
    dates = parsed.get('dates') if isinstance(parsed.get('dates'), list) else []
    risks = parsed.get('risks') if isinstance(parsed.get('risks'), list) else []
    next_steps = parsed.get('next_steps') if isinstance(parsed.get('next_steps'), list) else []

    data = {
        "job_id": job_id,
        "video_url": video_url,
        "transcription": transcription_text,
        "status": "processing",
        "created_at": datetime.utcnow().isoformat(),
        "datetime": datetime.utcnow().date().isoformat(),
        "title": title,
        "reuniao": file_name,
        "user_id": user_id,
        "meeting_type": meeting_type or "projeto",
    }
    if url_hash:
        data["url_hash"] = url_hash

    # Buscar registro existente por job_id ou url_hash
    transcription_id = None
    existing = None
    try:
        from services.supabase_service import get_supabase_service_client
        supabase = get_supabase_service_client()
        
        # Primeiro tentar por job_id
        if job_id:
            res = (
                supabase
                .table("transcriptions")
                .select("id, job_id, status")
                .eq("job_id", job_id)
                .limit(1)
                .execute()
            )
            data_result = getattr(res, "data", None)
            if data_result and len(data_result) > 0:
                existing = data_result[0]
                transcription_id = existing["id"]
        
        # Se não encontrou, tentar por url_hash
        if transcription_id is None and url_hash:
            existing = _find_transcription_by_hash(url_hash)
            if existing:
                transcription_id = existing["id"]
    except Exception as e:
        print(f"[DEBUG] Erro ao buscar registro existente: {e}")
        existing = None

    if transcription_id is None:
        res = insert_transcription(data, supabase_url=supabase_url, service_key=service_key)
        transcription_id = res.data[0]['id']
        print(f"[DEBUG] Novo registro criado com ID: {transcription_id}")
    else:
        print(f"[DEBUG] Usando registro existente com ID: {transcription_id}")

    print(f"[DEBUG] Preparando atualização para transcription_id: {transcription_id}")
    update_data = {
        "status": "completed",
        "transcription": transcription_text,
        "executive_summary": executive_summary,
        "decisions": json.dumps(decisions),
        "main_points": json.dumps(main_points),
        "action_items": json.dumps(action_items),
        "tags": json.dumps(tags),
        "client": client,
        "project": project,
        "rito": rito,
        "title": title,
        "reuniao": file_name,
        "participants": json.dumps(participants),
        "metrics": json.dumps(metrics),
        "dates": json.dumps(dates),
        "risks": json.dumps(risks),
        "next_steps": json.dumps(next_steps),
        "updated_at": datetime.utcnow().isoformat(),
        "meeting_type": meeting_type or "projeto",
        # Nota: include_nlp e speaker_labels não são salvos na tabela (apenas usados durante processamento)
    }
    
    print(f"[DEBUG] Atualizando transcrição {transcription_id} com status: {update_data['status']}")
    result = update_transcription(transcription_id, update_data)
    print(f"[DEBUG] Atualização concluída: {result}")
    print(f"[DEBUG] ✅ Transcrição {transcription_id} salva com sucesso no Supabase")


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/admin/clear-cache")
def clear_tenant_cache():
    """Limpa o cache de tenants para forçar nova busca no Registry"""
    from middleware.tenant import _tenant_cache
    cache_size = len(_tenant_cache)
    _tenant_cache.clear()
    return {
        "status": "success",
        "message": f"Cache limpo: {cache_size} entradas removidas",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/transcribe")
async def transcribe_from_url(req: TranscriptionRequest, current_user: dict = Depends(get_current_user_or_service)):
    try:
        job_id = str(uuid.uuid4())
        download = DownloadService()
        dl = download.download_file(req.video_url, job_id)
        if not dl["success"]:
            raise HTTPException(500, f"Erro no download: {dl['error']}")

        file_path = dl["file_path"]
        file_hash = dl.get("file_hash")
        url_hash = f"content:{file_hash}" if file_hash else f"url:{_sha256_str(req.video_url)}"

        # Evita duplicação dentro da mesma réplica
        lock = _get_lock_for(url_hash)
        with lock:
            existing = _find_transcription_by_hash(url_hash)
            if existing and existing.get("status") in ("processing", "completed"):
                return TranscriptionResponse(
                    job_id=existing.get("job_id") or job_id,
                    message="Transcrição já existente",
                    status=existing.get("status") or "done"
                )
        assembly = AssemblyAIService()
        upload = assembly.upload_file(file_path)
        if not upload.get("success"):
            raise HTTPException(500, f"Erro no upload: {upload.get('error')}")

        url = upload.get("upload_url")
        trans = assembly.start_transcription(url)
        if not trans.get("success"):
            raise HTTPException(500, f"Erro ao iniciar transcrição: {trans.get('error')}")
        final = assembly.wait_for_completion(trans.get("transcript_id"))
        if not final.get("success"):
            raise HTTPException(500, f"Erro na transcrição: {final.get('error')}")

        text = final["data"].get("text", "")
        user_id = current_user.get('id') if current_user else None
        process_and_save_transcription(
            text,
            job_id,
            req.video_url,
            req.title or os.path.basename(req.video_url),
            user_id,
            url_hash=url_hash,
            meeting_type=req.meeting_type,
        )

        return TranscriptionResponse(job_id=job_id, message="Transcrição concluída e salva no Supabase", status="done")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


def _process_upload_transcription(
    audio_path: str, 
    job_id: str, 
    filename: str, 
    user_id: Optional[str], 
    url_hash: str, 
    meeting_type: Optional[str] = None,
    include_nlp: bool = True,
    speaker_labels: bool = True,
    supabase_url: Optional[str] = None,
    service_key: Optional[str] = None
):
    """Processa transcrição em background para evitar timeout"""
    try:
        if supabase_url and service_key:
            print(f"[BACKGROUND] Usando credenciais explícitas | URL: {supabase_url[:50]}...")
        print(f"[BACKGROUND] Iniciando processamento de {filename}")
        print(f"[BACKGROUND] Configurações: include_nlp={include_nlp}, speaker_labels={speaker_labels}")
        download = DownloadService()
        assembly = AssemblyAIService()
        
        # Upload para AssemblyAI
        upload = assembly.upload_file(audio_path)
        if not upload.get("success"):
            print(f"[BACKGROUND] Erro no upload: {upload.get('error')}")
            return
        
        # ✅ Criar configuração para transcrição
        from services.assembly_service import TranscriptionConfig
        config = TranscriptionConfig(
            language_code="pt",
            speaker_labels=speaker_labels,
            auto_punctuation=True,
            format_text=True
        )
        
        # Iniciar transcrição com configuração
        trans = assembly.start_transcription(upload.get("upload_url"), config=config)
        if not trans.get("success"):
            print(f"[BACKGROUND] Erro ao iniciar transcrição: {trans.get('error')}")
            return
        
        # Aguardar conclusão
        final = assembly.wait_for_completion(trans.get("transcript_id"))
        if not final.get("success"):
            print(f"[BACKGROUND] Erro na transcrição: {final.get('error')}")
            return
        
        # Salvar no Supabase
        text = final["data"].get("text", "")
        process_and_save_transcription(
            text, 
            job_id, 
            "UPLOAD", 
            filename, 
            user_id, 
            url_hash=url_hash, 
            meeting_type=meeting_type,
            include_nlp=include_nlp,
            speaker_labels=speaker_labels,
            supabase_url=supabase_url,
            service_key=service_key
        )
        print(f"[BACKGROUND] Transcrição concluída e salva: {job_id}")
    except Exception as e:
        print(f"[BACKGROUND] Erro no processamento: {e}")
        import traceback
        traceback.print_exc()


@app.post("/api/transcribe/upload")
async def transcribe_upload(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...), 
    meeting_type: Optional[str] = Form(None),
    force: Optional[bool] = Form(False),  # ✅ Permitir reprocessamento
    include_nlp: Optional[bool] = Form(True),
    speaker_labels: Optional[bool] = Form(True),
    current_user: dict = Depends(get_current_user_optional)
):
    try:
        job_id = str(uuid.uuid4())
        temp_path = f"/tmp/{job_id}_{file.filename}"
        
        print(f"[UPLOAD] Recebendo arquivo: {file.filename}, content_type: {file.content_type}")
        
        # Salvar arquivo em chunks para evitar problemas de memória com arquivos grandes
        try:
            file_size = 0
            with open(temp_path, "wb") as f:
                while chunk := await file.read(8192):  # Ler em chunks de 8KB
                    f.write(chunk)
                    file_size += len(chunk)
            print(f"[UPLOAD] Arquivo salvo: {file_size} bytes ({file_size / 1024 / 1024:.2f} MB) em {temp_path}")
        except Exception as e:
            print(f"[UPLOAD] Erro ao salvar arquivo: {e}")
            raise HTTPException(500, f"Erro ao salvar arquivo: {str(e)}")

        download = DownloadService()
        audio_path = temp_path
        
        # Extrair áudio se for vídeo
        if file.content_type and file.content_type.startswith("video/"):
            print(f"[UPLOAD] Extraindo áudio de vídeo")
            audio_path = temp_path + ".audio.mp3"
            extraction = download._extract_audio_from_video(temp_path, audio_path)
            if not extraction["success"]:
                raise HTTPException(500, f"Erro ao extrair áudio: {extraction['error']}")

        # Idempotência baseada no conteúdo do arquivo
        file_hash = download._calculate_file_hash(audio_path)
        url_hash = f"upload:{file_hash}"

        lock = _get_lock_for(url_hash)
        with lock:
            # ✅ Verificar duplicação apenas se force=False
            if not force:
                existing = _find_transcription_by_hash(url_hash)
                if existing and existing.get("status") in ("processing", "completed"):
                    print(f"[UPLOAD] Transcrição já existe: {existing.get('job_id')}")
                    return TranscriptionResponse(
                        job_id=existing.get("job_id") or job_id,
                        message="Transcrição já existente",
                        status=existing.get("status") or "processing"
                    )
            else:
                print(f"[UPLOAD] Modo force ativado - ignorando verificação de duplicação para arquivo: {file.filename}")

        # Criar registro inicial no Supabase (verificando duplicata)
        user_id = current_user.get('id') if current_user else None
        try:
            # Verificar se já existe um registro com este hash
            existing = _find_transcription_by_hash(url_hash)
            if existing:
                print(f"[UPLOAD] Registro já existe no Supabase: {existing['job_id']}")
            else:
                initial_data = {
                    "job_id": job_id,
                    "video_url": "UPLOAD",
                    "reuniao": file.filename,
                    "status": "processing",
                    "url_hash": url_hash,
                }
                if user_id:
                    initial_data["user_id"] = user_id
                
                insert_transcription(initial_data)
                print(f"[UPLOAD] Registro inicial criado no Supabase: {job_id}")
        except Exception as e:
            print(f"[UPLOAD] Erro ao criar registro inicial: {e}")
            # Continuar mesmo se falhar
        
        # Capturar credenciais do tenant antes de iniciar background task
        from middleware.tenant import get_tenant_context
        tenant_ctx = get_tenant_context()
        
        # Debug detalhado
        print(f"[UPLOAD] DEBUG: tenant_slug = {tenant_ctx.tenant_slug}")
        print(f"[UPLOAD] DEBUG: tenant_data exists = {bool(tenant_ctx.tenant_data)}")
        if tenant_ctx.tenant_data:
            print(f"[UPLOAD] DEBUG: tenant_data keys = {list(tenant_ctx.tenant_data.keys())}")
        
        supabase_url = tenant_ctx.get_supabase_url() if tenant_ctx.tenant_data else None
        service_key = tenant_ctx.get_service_key() if tenant_ctx.tenant_data else None
        
        print(f"[UPLOAD] DEBUG: supabase_url = {supabase_url[:50] if supabase_url else None}")
        print(f"[UPLOAD] DEBUG: service_key exists = {bool(service_key)}")
        
        if supabase_url and service_key:
            print(f"[UPLOAD] ✅ Credenciais capturadas para background task | URL: {supabase_url[:50]}...")
        else:
            print(f"[UPLOAD] ❌ AVISO: Credenciais não disponíveis, background task usará fallback")
        
        # Processar em background para não bloquear a resposta HTTP
        background_tasks.add_task(
            _process_upload_transcription, 
            audio_path, 
            job_id, 
            file.filename, 
            user_id, 
            url_hash, 
            meeting_type,
            include_nlp,
            speaker_labels,
            supabase_url,
            service_key
        )
        
        print(f"[UPLOAD] Transcrição agendada em background: {job_id}")
        return TranscriptionResponse(
            job_id=job_id, 
            message="Transcrição iniciada em background", 
            status="processing"
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[UPLOAD] Erro: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)


