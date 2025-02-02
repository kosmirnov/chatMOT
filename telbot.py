import os
import requests
import openai
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext,ConversationHandler
import time

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("telegram_bot_token")
TOKEN_URL = os.getenv("token_url")
CLIENT_ID = os.getenv("client_id")
CLIENT_SECRET = os.getenv("client_secret")
SCOPE_URL = os.getenv("scope_url")
API_KEY = os.getenv("api_key")
OPENAI_API_KEY = os.getenv("open_ai_key")

# Configure OpenAI
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

MOT_QUERY, FOLLOW_UP = range(2)

def get_access_token():
    """Fetches an access token from the MoT API."""
    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": SCOPE_URL,
        "grant_type": "client_credentials"
    }

    try:
        response = requests.post(TOKEN_URL, data=token_data)
        response.raise_for_status()
        return response.json().get("access_token")
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error obtaining access token: {e}")
        return None


def fetch_vehicle_data(registration):
    """Fetches MoT history for a given vehicle registration number."""
    token = get_access_token()
    if not token:
        return None

    API_URL = f"https://history.mot.api.gov.uk/v1/trade/vehicles/registration/{registration}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-Api-Key": API_KEY
    }

    try:
        response = requests.get(API_URL, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error fetching vehicle data: {e}")
        return None


def generate_mot_summary(vehicle_data):
    """Generates an MoT summary using ChatGPT."""
    if not vehicle_data or "motTests" not in vehicle_data or not vehicle_data["motTests"]:
        return "No MoT test data available for this vehicle."

    # Prepare MoT summary
    mot_summary = f"Vehicle Registration: {vehicle_data.get('registration', 'Unknown')}\n"
    mot_summary += f"Make: {vehicle_data.get('make', 'Unknown')}\n"
    mot_summary += f"Model: {vehicle_data.get('model', 'Unknown')}\n"
    mot_summary += f"First Registered: {vehicle_data.get('firstUsedDate', 'Unknown')}\n\n"
    mot_summary += "MoT Test History:\n"

    for test in vehicle_data["motTests"]:
        mot_summary += f"- Test Date: {test.get('completedDate', 'N/A')}, "
        mot_summary += f"Result: {'Pass ✅' if test.get('testResult') == 'PASSED' else 'Fail ❌'}\n"
        mot_summary += f"  Mileage: {test.get('odometerValue', 'N/A')} {test.get('odometerUnit', '')}\n"

        for i in test.get('defects', []):
            mot_summary += f"  Defect: {i.get('text', 'N/A')} (Type: {i.get('type', 'N/A')}, Dangerous: {i.get('dangerous', 'N/A')})\n"

    chatgpt_prompt = f"""
    Summarize the following UK MoT vehicle history in a concise, human-readable format:

    {mot_summary}

    The summary should highlight key points in a professional manner. 
    It should highlight major defects in the recent MoTs. 
    Also provide a warning if body structure corrosion of the vehicle has been detected. Cite the report and date where this has been detected. 
    Provide warnings if any dangerous MoT defects have been identified in the past.
    Also consider the make and model of the car in terms of classifying the condition of the vehicle, e.g. it is quite common for a Defender to leak oil.
    Or a Discovery to leak water from the sunroof. Be a bit funny when it comes to Land Rovers as they are known for unreliability, but don't be too funny, just a bit cynical.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert vehicle report summarizer."},
                {"role": "user", "content": chatgpt_prompt}
            ],
            temperature=0.5,
            max_tokens=1000
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"⚠️ Error generating summary: {e}"


async def handle_message(update: Update, context: CallbackContext) -> None:
    """Handles user messages (vehicle registration numbers)."""
    user_input = update.message.text.strip().upper()

    await update.message.reply_text(f"🔍 Fetching MoT history for: {user_input}...")

    vehicle_data = fetch_vehicle_data(user_input)

    if not vehicle_data:
        await update.message.reply_text("⚠️ No MoT history found. Please check the registration number and try again.")
        return

    summary = generate_mot_summary(vehicle_data)
    await update.message.reply_text(f"📌 **MoT Summary:**\n{summary}")

    # Enable follow-up questions
    context.user_data["mot_summary"] = summary
    await update.message.reply_text(
        "💬 You can ask further questions about this vehicle's history. Type your question or type 'exit' to stop.")

    return FOLLOW_UP

async def answer_follow_up_questions(update: Update, context: CallbackContext) -> None:
    """Handles follow-up questions about the MoT summary."""
    user_question = update.message.text.strip()

    if user_question.lower() == "exit":
        await update.message.reply_text("👋 Exiting chat. Have a great day!")
        return ConversationHandler.END

    mot_summary = context.user_data.get("mot_summary", "No MoT summary available.")

    chatgpt_prompt = f"""
    Here is the vehicle MoT summary:\n{mot_summary}\n\n
    Answer the following question about this vehicle's MoT history:\n{user_question}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert in UK MoT vehicle history."},
                {"role": "user", "content": chatgpt_prompt}
            ],
            temperature=0.5,
            max_tokens=500
        )

        answer = response.choices[0].message.content.strip()
        await update.message.reply_text(f"🤖 AI: {answer}")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error generating response: {e}")

    return FOLLOW_UP
def main():
    """Starts the bot."""
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        states={
            FOLLOW_UP: [MessageHandler(filters.TEXT & ~filters.COMMAND, answer_follow_up_questions)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", handle_message))
    app.add_handler(conv_handler)

    logging.info("🚀 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
    while True:
        time.sleep(10)  # Prevents Railway from shutting down the bot