import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from pymongo import MongoClient
import datetime
import re
import os

# CONFIGURACIÓN PRINCIPAL
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not GEMINI_API_KEY or not TELEGRAM_BOT_TOKEN or not MONGO_URI:
    raise ValueError("⚠️ Faltan las variables de entorno GEMINI_API_KEY, TELEGRAM_BOT_TOKEN o MONGO_URI")

# Configurar Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Conectar a MongoDB
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["bot_turistico"]
conversaciones = db["historiales"]

def guardar_historial(usuario, mensaje_usuario, respuesta_bot, sentimiento):
    """Guarda la conversación del usuario en MongoDB"""
    try:
        fecha = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        conversaciones.insert_one({
            "usuario": usuario,
            "fecha": fecha,
            "mensaje_usuario": mensaje_usuario,
            "respuesta_bot": respuesta_bot,
            "sentimiento": sentimiento
        })
        print(f"✅ Historial guardado correctamente para {usuario}")
    except Exception as e:
        print(f"❌ Error al guardar historial: {e}")


async def analizar_sentimiento(texto: str) -> str:
    """Devuelve 'positivo', 'negativo' o 'neutral'"""
    model = genai.GenerativeModel("models/gemini-2.5-flash")
    prompt = f"Analizá el siguiente texto y respondé solo con 'positivo', 'negativo' o 'neutral': {texto}"
    result = model.generate_content(prompt)
    return result.text.strip().lower()


async def generar_respuesta(prompt: str, categoria: str, incluir_maps: bool) -> str:
    """Genera una respuesta turística adaptada a la categoría"""
    model = genai.GenerativeModel("models/gemini-2.5-flash")

    # Contexto según la categoría
    if categoria == "destinos":
        contexto = "Brindá información turística sobre el destino, historia, atractivos y ubicación."
    elif categoria == "gastronomia":
        contexto = "Hablá sobre la gastronomía típica del lugar, platos tradicionales y recomendaciones culinarias."
    elif categoria == "actividades":
        contexto = "Describí actividades, excursiones o experiencias que se puedan realizar en el lugar."
    else:
        contexto = "Ofrecé información general de turismo."

    # Solo agregar link si el usuario lo pide
    if incluir_maps:
        instrucciones_maps = (
            "Si corresponde, incluí un enlace REAL de Google Maps con el formato:\n"
            "https://www.google.com/maps/search/?api=1&query=Nombre+del+lugar"
        )
    else:
        instrucciones_maps = "No incluyas enlaces de Google Maps ni ubicaciones."

    response = model.generate_content(
        f"""
        Actuá como un guía turístico profesional.
        Respondé de forma breve (máx. 8 líneas), clara y atractiva.
        Usá emojis y estilo amigable, pero NO saludes ni uses frases iniciales como 'Hola' o 'Bienvenido'.
        {instrucciones_maps}

        Contexto: {contexto}
        Pregunta del usuario: {prompt}
        """
    )

    return response.text.strip()


# FUNCIONES PRINCIPALES
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🌆 Destinos", callback_data="destinos"),
            InlineKeyboardButton("🍽️ Gastronomía", callback_data="gastronomia")
        ],
        [
            InlineKeyboardButton("🎢 Actividades", callback_data="actividades"),
            InlineKeyboardButton("ℹ️ Info", callback_data="info")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "¡Hola! Soy tu asistente turístico 🤖🌍\n"
        "Elegí una categoría para comenzar:",
        reply_markup=reply_markup
    )


async def boton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    categoria = query.data
    context.user_data["categoria"] = categoria

    if categoria == "destinos":
        await query.edit_message_text("🌍 Escribime el destino del que querés recibir recomendaciones turísticas.")
    elif categoria == "gastronomia":
        await query.edit_message_text("🍽️ Indicame una ciudad o país y te cuento sobre su gastronomía típica.")
    elif categoria == "actividades":
        await query.edit_message_text("🎢 Decime un destino y te sugiero actividades o excursiones para hacer.")
    elif categoria == "info":
        await query.edit_message_text(
            "🤖 *Bot Turístico con IA (Gemini 2.5 Flash)*\n\n"
            "Desarrollado con Python y Telegram Bot API.\n"
            "Ofrece información sobre destinos, gastronomía y actividades.\n"
            "Incluye análisis de sentimientos y registro de historial en MongoDB.\n"
            "_Proyecto educativo del Instituto Tecnológico Universitario._",
            parse_mode="Markdown"
        )


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = update.message.text
        usuario = update.message.from_user.first_name
        categoria = context.user_data.get("categoria", "general")

        # Detectar si el usuario pide link o ubicación
        palabras_clave_maps = ["mapa", "ubicación", "dónde queda", "cómo llegar", "link", "google maps"]
        incluir_maps = any(palabra in prompt.lower() for palabra in palabras_clave_maps)

        # Analizar sentimiento
        sentimiento = await analizar_sentimiento(prompt)

        # Generar respuesta
        respuesta = await generar_respuesta(prompt, categoria, incluir_maps)

        # Ajustar según sentimiento
        if "negativo" in sentimiento:
            respuesta = "😕 Parece que no estás del todo conforme. Espero poder ayudarte mejor.\n\n" + respuesta
        elif "positivo" in sentimiento:
            respuesta = "😊 Me alegra tu entusiasmo.\n\n" + respuesta
        else:
            respuesta = "🙂 Entendido.\n\n" + respuesta

        # Limpiar duplicados tipo [https://...](https://...)
        respuesta = re.sub(
            r"\[https?://[^\]]+\]\(https?://[^\)]+\)",
            lambda m: m.group(0).split('](')[0][1:],
            respuesta
        )

        # Guardar historial del usuario en MongoDB
        guardar_historial(usuario, prompt, respuesta, sentimiento)

        # Enviar respuesta en partes si es muy larga
        MAX_LENGTH = 4000
        partes = [respuesta[i:i + MAX_LENGTH] for i in range(0, len(respuesta), MAX_LENGTH)]
        for parte in partes:
            await update.message.reply_text(parte, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        await update.message.reply_text("😕 Ocurrió un error al generar la respuesta. Intentalo de nuevo.")
        print(f"Error: {e}")


# CONFIGURACIÓN PRINCIPAL
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(boton))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))

    print("🤖 Bot turístico en marcha con almacenamiento en MongoDB")
    app.run_polling()


# EJECUCIÓN
if __name__ == "__main__":
    main()