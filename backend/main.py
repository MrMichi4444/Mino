from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Header, HTTPException, status, Form, FastAPI, Depends, Security
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types
from datetime import datetime
from supabase import create_client, Client
import os
import csv
import time
import urllib.request
import json

load_dotenv()

# Inicializamos el cliente de Gemini
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL") 

app = FastAPI(
    title="Mino API",
    description="Backend de Mino - Salud felina",
    version="1.0.0",
)

security = HTTPBearer()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# DEFINIMOS LA RUTA ABSOLUTA AQUÍ PARA QUE RENDER NO SE PIERDA
DIRECTORIO_ACTUAL = os.path.dirname(os.path.abspath(__file__))
RUTA_CSV = os.path.join(DIRECTORIO_ACTUAL, "interesados_mino.csv")

SYSTEM_PROMPT = """
Eres Sweete, la IA compañera de Mino, una aplicación de salud felina. 
Tu única función es ayudar a dueños de gatos preocupados a evaluar 
los síntomas de su michi y decidir si necesitan atención veterinaria.

## PERSONALIDAD
Eres cariñosa, empática y cercana. Hablas como un amigo que sabe 
mucho de gatos, no como un médico frío. Usas lenguaje simple y 
coloquial en español. Puedes usar ocasionalmente palabras como 
"michi", "gatito" o "peludo". Nunca eres dramática ni alarmista, 
pero tampoco minimizas lo que puede ser serio.

## FLUJO DE LA CONVERSACIÓN
Sigues este flujo estrictamente en este orden:

1. RECEPCIÓN: El usuario llega con un síntoma. Tu primer mensaje 
   siempre tiene dos partes: primero una frase corta de empatía, 
   luego tu primera pregunta de seguimiento.

2. TRIAJE: Haces máximo 2 preguntas de seguimiento, nunca más.
   Las preguntas deben ser cortas, simples y una a la vez.
   Nunca hagas dos preguntas en el mismo mensaje.

3. VEREDICTO: Después de máximo 2 preguntas, emites tu veredicto.
   Existen exactamente 3 posibles veredictos:

   VEREDICTO URGENTE: cuando detectas señales de alarma graves.
   Mensaje: "Con lo que me contás, esto merece atención hoy. 
   No es para asustarte, pero algunos de estos síntomas juntos 
   son señal de que un veterinario debe verlo pronto. 
   [Razón breve en una oración]. Recuerda que soy una IA y no 
   reemplazo la opinión de un veterinario real."

   VEREDICTO MONITOREO: cuando los síntomas no son urgentes 
   pero tampoco triviales.
   Mensaje: "Por lo que me contás, no parece una emergencia, 
   pero sí vale la pena estar atentos. Te propongo que lo 
   monitoreemos juntos los próximos 3 días. Recuerda que soy 
   una IA y no reemplazo la opinión de un veterinario real."

   VEREDICTO TRANQUILO: cuando los síntomas son claramente menores.
   Mensaje: "Por lo que me contás, no parece nada grave. 
   Los gatos a veces tienen estos episodios sin que sea señal 
   de algo serio. Recuerda que soy una IA y no reemplazo la 
   opinión de un veterinario real."

4. CAPTURA DE CONTACTO: después del veredicto URGENTE o MONITOREO:
   "¿Me dejás tu número de WhatsApp o correo para recordarte 
   hacer el seguimiento? Es opcional, pero ayuda mucho."

## SEÑALES DE ALARMA (siempre activan VEREDICTO URGENTE)
- Dificultad para respirar
- No ha orinado en más de 24 horas o llora al intentarlo
- Vómito con sangre o más de 4 veces en pocas horas
- Convulsiones o pérdida de consciencia
- Caída o incapacidad de caminar
- Abdomen hinchado o duro
- Trauma físico
- No responde a estímulos

## REGLAS ABSOLUTAS
- Nunca más de 2 preguntas de seguimiento
- Nunca emitas un diagnóstico médico
- Nunca sugieras medicamentos ni tratamientos
- Responde siempre en español
- Mensajes cortos, máximo 3 oraciones
- Si detectas señal de alarma en cualquier momento, 
  emite VEREDICTO URGENTE inmediatamente
"""

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    try:
        token = credentials.credentials
        user = supabase.auth.get_user(token)
        return user.user
    except:
        raise HTTPException(status_code=401, detail="Token inválido")

class AuthRequest(BaseModel):
    email: str
    password: str

@app.post("/auth/registro")
async def registro(data: AuthRequest):
    try:
        res = supabase.auth.sign_up({"email": data.email, "password": data.password})
        return {"user_id": res.user.id, "email": res.user.email}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/login")
async def login(data: AuthRequest):
    try:
        res = supabase.auth.sign_in_with_password({"email": data.email, "password": data.password})
        return {
            "access_token": res.session.access_token,
            "user_id": res.user.id
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

# --- ENDPOINTS DE GATO ---

class GatoRequest(BaseModel):
    nombre: str
    fecha_nacimiento: str | None = None
    raza: str | None = None

@app.post("/gato")
async def crear_gato(data: GatoRequest, user=Depends(get_current_user)):
    # Verificar que no tenga ya un gato
    existente = supabase.table("gatos").select("id").eq("usuario_id", user.id).execute()
    if existente.data:
        raise HTTPException(status_code=400, detail="Ya tienes un gato registrado")
    
    res = supabase.table("gatos").insert({
        "usuario_id": user.id,
        "nombre": data.nombre,
        "fecha_nacimiento": data.fecha_nacimiento,
        "raza": data.raza
    }).execute()
    return res.data[0]

@app.get("/gato")
async def obtener_gato(user=Depends(get_current_user)):
    res = supabase.table("gatos").select("*").eq("usuario_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="No tienes un gato registrado")
    return res.data[0]

@app.patch("/gato")
async def editar_gato(data: GatoRequest, user=Depends(get_current_user)):
    gato = supabase.table("gatos").select("id").eq("usuario_id", user.id).execute()
    if not gato.data:
        raise HTTPException(status_code=404, detail="No tienes un gato registrado")
    
    res = supabase.table("gatos").update({
        "nombre": data.nombre,
        "fecha_nacimiento": data.fecha_nacimiento,
        "raza": data.raza
    }).eq("id", gato.data[0]["id"]).execute()
    return res.data[0]

# Cambiado a @app.get y utilizando la RUTA_CSV
@app.get("/descargar-csv")
def descargar_csv():
    if os.path.exists(RUTA_CSV):
        return FileResponse(
            path=RUTA_CSV, 
            filename="interesados_mino.csv",
            media_type='text/csv'
        )
    return {"error": f"El archivo no se encontró. El sistema buscó en: {RUTA_CSV}"}

@app.post("/chat")
async def chat(request: ChatRequest):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            history = []
            for msg in request.messages[:-1]:
                history.append(types.Content(
                    role="user" if msg.role == "user" else "model",
                    parts=[types.Part(text=msg.content)]
                ))
            
            last_message = request.messages[-1].content
            
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                ),
                contents=history + [types.Content(
                    role="user",
                    parts=[types.Part(text=last_message)]
                )]
            )
            
            return {"response": response.text}
            
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                time.sleep(10)
                continue
            raise e

@app.post("/registrar-correo")
async def registrar_correo(email: str = Form(...)):
    # --- PARTE 1: Guardar en CSV local ---
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(RUTA_CSV, mode="a", newline="") as file:
            writer = csv.writer(file)
            writer.writerow([fecha, email])
    except Exception as e:
        print(f"Error al escribir en CSV: {e}")

    # --- PARTE 2: Notificar a Discord ---
    payload = {
        "content": f"🐾 **¡Nuevo Dueño Fundador de Mino!**\nEmail: `{email}`"
    }
    
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL, 
        data=json.dumps(payload).encode('utf-8'), 
        headers={
            'Content-Type': 'application/json', 
            'User-Agent': 'MinoBot'
        }
    )
    
    try:
        urllib.request.urlopen(req)
        print(f"Correo {email} enviado a Discord exitosamente.")
    except Exception as e:
        print(f"Error al enviar a Discord: {e}")
        
    return {"status": "success", "message": "Correo registrado con éxito"}
