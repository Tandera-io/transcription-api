# Tandera Transcription API

API de transcrição (AssemblyAI) + enriquecimento OpenAI separada do monolito.

## Endpoints
- POST `/api/transcribe` { video_url }
- POST `/api/transcribe/upload` (multipart file)
- GET `/api/health`

Autenticação: Supabase JWT via header `Authorization: Bearer <token>`.

### Idempotência e reprocessamento
- O serviço usa `url_hash` do conteúdo para não duplicar transcrições.
- Se já houver registro `completed`, a API retorna imediatamente esse job.
- Se houver registro `processing`, a API considera STALE após `PROCESSING_STALE_MINUTES` (padrão 120) e reprocessa.
- Para forçar reprocessamento imediato:
  - URL: envie `{"video_url": "...", "force": true}`.
  - Upload: use query `?force=true` no endpoint de upload.

## Variáveis de ambiente
- SUPABASE_URL
- SUPABASE_KEY
- SUPABASE_JWT_SECRET
- ASSEMBLYAI_API_KEY
- OPENAI_API_KEY
- CORS_ORIGINS (opcional)

## Deploy Railway
- Runtime: Python 3.11
- Procfile: `web: uvicorn main:app --host 0.0.0.0 --port 8080`
- Build: `pip install -r requirements.txt`

## Execução local
```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```
