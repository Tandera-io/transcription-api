# Tandera Transcription API

API de transcrição (AssemblyAI) + enriquecimento OpenAI separada do monolito.

## Endpoints
- POST `/api/transcribe` { video_url }
- POST `/api/transcribe/upload` (multipart file)
- GET `/api/health`

Autenticação: Supabase JWT via header `Authorization: Bearer <token>`.

## Variáveis de ambiente
- SUPABASE_URL
- SUPABASE_KEY
- SUPABASE_JWT_SECRET
- ASSEMBLYAI_API_KEY
- OPENAI_API_KEY
- TRANSCRIPTION_SERVICE_API_KEY (para autenticação service-to-service)
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
