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
import hashlib
import threading

from supabase import create_client, Client

from services.assembly_service import AssemblyAIService
from services.download_service import DownloadService
from services.supabase_service import insert_transcription, update_transcription
from services.openai_service import gpt_4_completion
from middleware.auth import get_current_user


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
            .select("id, job_id, status")
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
                                   user_id: Optional[str] = None, url_hash: Optional[str] = None):
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

    # Se já existir um registro com o mesmo hash, não inserir outro
    transcription_id = None
    existing = None
    try:
        if url_hash:
            existing = _find_transcription_by_hash(url_hash)
            if existing:
                transcription_id = existing["id"]
    except Exception:
        existing = None

    if transcription_id is None:
        res = insert_transcription(data)
        transcription_id = res.data[0]['id']

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
    update_transcription(transcription_id, update_data)


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/transcribe")
async def transcribe_from_url(req: TranscriptionRequest, current_user: dict = Depends(get_current_user)):
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
        )

        return TranscriptionResponse(job_id=job_id, message="Transcrição concluída e salva no Supabase", status="done")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/transcribe/upload")
async def transcribe_upload(background_tasks: BackgroundTasks, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    try:
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
        with lock:
            existing = _find_transcription_by_hash(url_hash)
            if existing and existing.get("status") in ("processing", "completed"):
                return TranscriptionResponse(
                    job_id=existing.get("job_id") or job_id,
                    message="Transcrição já existente",
                    status=existing.get("status") or "done"
                )

        upload = assembly.upload_file(audio_path)
        if not upload.get("success"):
            raise HTTPException(500, f"Erro no upload: {upload.get('error')}")
        trans = assembly.start_transcription(upload.get("upload_url"))
        if not trans.get("success"):
            raise HTTPException(500, f"Erro ao iniciar transcrição: {trans.get('error')}")
        final = assembly.wait_for_completion(trans.get("transcript_id"))
        if not final.get("success"):
            raise HTTPException(500, f"Erro na transcrição: {final.get('error')}")

        text = final["data"].get("text", "")
        user_id = current_user.get('id') if current_user else None
        process_and_save_transcription(text, job_id, "UPLOAD", file.filename, user_id, url_hash=url_hash)

        return TranscriptionResponse(job_id=job_id, message="Transcrição concluída e salva no Supabase", status="done")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)


