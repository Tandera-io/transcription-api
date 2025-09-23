from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import os
import uuid
import json
import urllib.parse
import hashlib
import threading

from supabase import create_client, Client



class TranscriptionRequest(BaseModel):
    video_url: str
    title: Optional[str] = None
    meeting_type: Optional[str] = None
    participants: Optional[List[str]] = None
    force: Optional[bool] = False


class TranscriptionResponse(BaseModel):
    job_id: str
    message: str
    status: str


app = FastAPI(title="Tandera Transcription API", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    """Log environment status without blocking startup"""
    required_vars = ["SUPABASE_URL", "SUPABASE_KEY", "OPENAI_API_KEY", "ASSEMBLYAI_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"⚠️  Missing environment variables: {', '.join(missing_vars)}")
        print("ℹ️  Some features may not work until these are configured")
    else:
        print("✅ All required environment variables are configured")

cors_env = os.getenv("CORS_ORIGINS", "").strip()
if cors_env:
    allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
else:
    allowed_origins = [
        "*"
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
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
    if not _supabase_client or not url_hash:
        return None
    try:
        res = (
            _supabase_client
            .table("transcriptions")
            .select("id, job_id, status, created_at")
            .eq("url_hash", url_hash)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None)
        if data and len(data) > 0:
            return data[0]
    except Exception:
        return None
    return None


def _is_stale_processing(record: Dict[str, Any], stale_minutes: int = None) -> bool:
    """Return True if a processing record is considered stale based on created_at."""
    try:
        from datetime import timezone, timedelta
        minutes = stale_minutes or int(os.getenv("PROCESSING_STALE_MINUTES", "120"))
        created_at_str = record.get("created_at")
        if not created_at_str:
            return False
        # Support both isoformat with/without timezone
        try:
            created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except Exception:
            return False
        now = datetime.now(tz=created.tzinfo or timezone.utc)
        age = now - created
        return age >= timedelta(minutes=minutes)
    except Exception:
        return False


def _clean_filename_from_url(url: str) -> str:
    """Extract and clean filename from URL, removing URL encoding and query parameters"""
    try:
        parsed_url = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed_url.path)
        clean_filename = urllib.parse.unquote(filename)
        if not clean_filename or clean_filename == '/':
            clean_filename = "video_file"
        print(f"[DEBUG] Cleaned filename: '{url}' -> '{clean_filename}'")
        return clean_filename
    except Exception as e:
        print(f"[ERROR] Failed to clean filename from URL: {str(e)}")
        try:
            return os.path.basename(url.split('?')[0])
        except Exception:
            return "video_file"


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


def process_and_save_transcription(transcription_text: str, job_id: str, video_url: str, file_name: str,
                                   user_id: Optional[str] = None, url_hash: Optional[str] = None,
                                   allow_duplicate_insert: bool = False):
    from services.openai_service import gpt_4_completion
    from services.supabase_service import insert_transcription, update_transcription
    print(f"[DEBUG] Starting process_and_save_transcription for job_id: {job_id}")
    print(f"[DEBUG] Transcription text length: {len(transcription_text)} characters")
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
        print(f"[DEBUG] Calling OpenAI GPT-4 for analysis...")
        result_text = gpt_4_completion(prompt, max_tokens=2000)
        print(f"[DEBUG] OpenAI response received, length: {len(result_text)} characters")
        parsed = _extract_json_from_text(result_text)
        print(f"[DEBUG] JSON parsing successful, keys: {list(parsed.keys())}")
    except Exception as e:
        print(f"[ERROR] OpenAI processing failed: {str(e)}")
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

    data = {
        "job_id": job_id,
        "video_url": video_url,
        "transcription": transcription_text,
        "status": "processing",
        "created_at": datetime.utcnow().isoformat(),
        "title": title,
        "reuniao": file_name,
        "user_id": user_id,
    }
    if url_hash:
        data["url_hash"] = url_hash
    # Se já existir um registro com o mesmo hash, não inserir outro (a menos que seja uma cópia)
    transcription_id = None
    existing = None
    try:
        if url_hash and not allow_duplicate_insert:
            existing = _find_transcription_by_hash(url_hash)
            if existing:
                transcription_id = existing["id"]
    except Exception:
        existing = None
    if transcription_id is None:
        print(f"[DEBUG] Inserting transcription data into Supabase...")
        res = insert_transcription(data)
        if not res or not hasattr(res, 'data') or not res.data:
            raise Exception(f"Insert failed - invalid response: {res}")
        transcription_id = res.data[0]['id']
        print(f"[DEBUG] Transcription inserted with ID: {transcription_id}")

    update_data = {
        "status": "completed",
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
    }
    try:
        print(f"[DEBUG] Updating transcription {transcription_id} with analysis data...")
        update_result = update_transcription(transcription_id, update_data)
        print(f"[DEBUG] Update successful: {update_result}")
    except Exception as e:
        print(f"[ERROR] Failed to update transcription: {str(e)}")
        raise Exception(f"Database update failed: {str(e)}")


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "transcription-api",
        "version": "1.0.0"
    }

@app.get("/health")
def health_simple():
    """Alternative health check endpoint without /api prefix"""
    return {"status": "ok"}


def get_current_user_lazy():
    from middleware.auth import get_current_user
    return get_current_user

def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    """Optional authentication - returns None if no valid token provided"""
    import jwt
    import os
    
    try:
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            print("[DEBUG] No authorization header found, proceeding without authentication")
            return None
            
        token = auth_header.split(" ")[1]
        jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
        
        if not jwt_secret:
            print("[DEBUG] JWT secret not configured, proceeding without authentication")
            return None
            
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        user_data = {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role", "user")
        }
        print(f"[DEBUG] Authentication successful for user: {user_data.get('email')}")
        return user_data
        
    except Exception as e:
        print(f"[DEBUG] Authentication failed: {str(e)}, proceeding without authentication")
        return None

@app.post("/api/transcribe")
async def transcribe_from_url(req: TranscriptionRequest, current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    try:
        from services.assembly_service import AssemblyAIService
        from services.download_service import DownloadService
        
        job_id = str(uuid.uuid4())
        download = DownloadService()
        dl = download.download_file(req.video_url, job_id)
        if not dl["success"]:
            raise HTTPException(500, f"Erro no download: {dl['error']}")

        file_path = dl["file_path"]
        file_hash = dl.get("file_hash")
        url_hash = f"content:{file_hash}" if file_hash else f"url:{_sha256_str(req.video_url)}"

        # Evita duplicação dentro da mesma réplica
        is_copy_run = False
        lock = _get_lock_for(url_hash)
        with lock:
            existing = _find_transcription_by_hash(url_hash)
            if existing:
                status = existing.get("status")
                is_stale = status == "processing" and _is_stale_processing(existing)
                if req.force:
                    print(f"[DEBUG] Force=true - ignorando dedupe. Existing id={existing.get('id')} status={status} stale={is_stale}")
                elif status == "completed":
                    # Nova regra: processar normalmente e prefixar o nome como cópia
                    is_copy_run = True
                    print(f"[DEBUG] Dedup hit (completed). Proceeding with copy run for url_hash={url_hash}")
                elif status == "processing" and not is_stale:
                    print(f"[DEBUG] Dedup hit (processing, not stale). Returning existing job {existing.get('job_id')} for url_hash={url_hash}")
                    return TranscriptionResponse(
                        job_id=existing.get("job_id") or job_id,
                        message="Transcrição em processamento. Reenvie com force=true para reprocessar",
                        status="processing"
                    )
                else:
                    print(f"[DEBUG] Existing processing appears stale. Continuing to reprocess. id={existing.get('id')}")
        assembly = AssemblyAIService()
        upload = assembly.upload_file(file_path)
        if not upload.get("success"):
            raise HTTPException(500, f"Erro no upload: {upload.get('error')}")

        url = upload.get("upload_url")
        if not url:
            raise HTTPException(500, "URL de upload não encontrada")
        trans = assembly.start_transcription(url)
        if not trans.get("success"):
            raise HTTPException(500, f"Erro ao iniciar transcrição: {trans.get('error')}")
        transcript_id = trans.get("transcript_id")
        if not transcript_id:
            raise HTTPException(500, "ID da transcrição não encontrado")
        final = assembly.wait_for_completion(transcript_id)
        if not final.get("success"):
            raise HTTPException(500, f"Erro na transcrição: {final.get('error')}")

        text = final["data"].get("text", "")
        user_id = current_user.get('id') if current_user else None
        clean_filename = req.title or _clean_filename_from_url(req.video_url)
        if is_copy_run:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            clean_filename = f"[copy-{ts}]" + clean_filename
        print(f"[DEBUG] URL transcription completed, text length: {len(text)} characters")
        print(f"[DEBUG] Current user: {current_user}")
        print(f"[DEBUG] User ID: {user_id}")
        print(f"[DEBUG] Clean filename: {clean_filename}")
        print(f"[DEBUG] Starting post-processing for job_id: {job_id}")
        process_and_save_transcription(
            text,
            job_id,
            req.video_url,
            clean_filename,
            user_id,
            url_hash=url_hash,
            allow_duplicate_insert=is_copy_run,
        )
        print(f"[DEBUG] Post-processing completed successfully for job_id: {job_id}")

        return TranscriptionResponse(job_id=job_id, message="Transcrição concluída e salva no Supabase", status="done")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] URL transcribe endpoint failed: {str(e)}")
        import traceback
        print(f"[ERROR] Full traceback: {traceback.format_exc()}")
        raise HTTPException(500, f"Erro interno: {str(e)}")


@app.post("/api/transcribe/upload")
async def transcribe_upload(background_tasks: BackgroundTasks, file: UploadFile = File(...), force: bool = False, current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    try:
        from services.assembly_service import AssemblyAIService
        from services.download_service import DownloadService
        
        job_id = str(uuid.uuid4())
        temp_path = f"/tmp/{job_id}_{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(await file.read())

        download = DownloadService()
        assembly = AssemblyAIService()

        audio_path = temp_path
        if file.content_type and file.content_type.startswith("video/"):
            audio_path = temp_path + ".audio.mp3"
            extraction = download._extract_audio_from_video(temp_path, audio_path)
            if not extraction["success"]:
                raise HTTPException(500, f"Erro ao extrair áudio: {extraction['error']}")

        # Idempotência baseada no conteúdo do arquivo
        file_hash = download._calculate_file_hash(audio_path)
        url_hash = f"upload:{file_hash}"

        lock = _get_lock_for(url_hash)
        is_copy_run = False
        with lock:
            existing = _find_transcription_by_hash(url_hash)
            if existing:
                status = existing.get("status")
                is_stale = status == "processing" and _is_stale_processing(existing)
                if force:
                    print(f"[DEBUG] Force=true - ignorando dedupe. Existing id={existing.get('id')} status={status} stale={is_stale}")
                elif status == "completed":
                    # Nova regra: processar normalmente e prefixar o nome como cópia
                    is_copy_run = True
                    print(f"[DEBUG] Dedup hit (completed). Proceeding with copy run for url_hash={url_hash}")
                elif status == "processing" and not is_stale:
                    print(f"[DEBUG] Dedup hit (processing, not stale). Returning existing job {existing.get('job_id')} for url_hash={url_hash}")
                    return TranscriptionResponse(
                        job_id=existing.get("job_id") or job_id,
                        message="Transcrição em processamento. Reenvie com force=true para reprocessar",
                        status="processing"
                    )
                else:
                    print(f"[DEBUG] Existing processing appears stale. Continuing to reprocess. id={existing.get('id')}")

        upload = assembly.upload_file(audio_path)
        if not upload.get("success"):
            raise HTTPException(500, f"Erro no upload: {upload.get('error')}")
        upload_url = upload.get("upload_url")
        if not upload_url:
            raise HTTPException(500, "URL de upload não encontrada")
        trans = assembly.start_transcription(upload_url)
        if not trans.get("success"):
            raise HTTPException(500, f"Erro ao iniciar transcrição: {trans.get('error')}")
        transcript_id = trans.get("transcript_id")
        if not transcript_id:
            raise HTTPException(500, "ID da transcrição não encontrado")
        final = assembly.wait_for_completion(transcript_id)
        if not final.get("success"):
            raise HTTPException(500, f"Erro na transcrição: {final.get('error')}")

        text = final["data"].get("text", "")
        user_id = current_user.get('id') if current_user else None
        print(f"[DEBUG] Transcription completed, text length: {len(text)} characters")
        print(f"[DEBUG] Current user: {current_user}")
        print(f"[DEBUG] User ID: {user_id}")
        print(f"[DEBUG] Starting post-processing for job_id: {job_id}")
        save_name = file.filename
        if is_copy_run:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            save_name = f"[copy-{ts}]" + save_name
        process_and_save_transcription(text, job_id, "UPLOAD", save_name, user_id, url_hash=url_hash, allow_duplicate_insert=is_copy_run)
        print(f"[DEBUG] Post-processing completed successfully for job_id: {job_id}")

        return TranscriptionResponse(job_id=job_id, message="Transcrição concluída e salva no Supabase", status="done")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Upload endpoint failed: {str(e)}")
        import traceback
        print(f"[ERROR] Full traceback: {traceback.format_exc()}")
        raise HTTPException(500, f"Erro interno: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)


