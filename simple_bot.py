"""
Simple Telegram Quiz Bot implementation
"""
import os
import json
import random
import logging
import re
import requests
from urllib.parse import urlparse
from telegram import Update, Poll, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, PollHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Define conversation states
QUESTION, OPTIONS, ANSWER = range(3)
EDIT_SELECT, EDIT_QUESTION, EDIT_OPTIONS, EDIT_ANSWER = range(3, 7)
CLONE_URL, CLONE_MANUAL = range(7, 9)

# Get bot token from environment
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Create data directory if it doesn't exist
os.makedirs('data', exist_ok=True)

# File paths
QUESTIONS_FILE = 'data/questions.json'
USERS_FILE = 'data/users.json'

def load_questions():
    """Load questions from the JSON file"""
    try:
        if os.path.exists(QUESTIONS_FILE):
            with open(QUESTIONS_FILE, 'r', encoding='utf-8') as file:
                questions = json.load(file)
            logger.info(f"Loaded {len(questions)} questions")
            return questions
        else:
            # Create sample questions if file doesn't exist
            questions = [
                {
                    "id": 1,
                    "question": "What is the capital of France?",
                    "options": ["Berlin", "Madrid", "Paris", "Rome"],
                    "answer": 2,  # Paris (0-based index)
                    "category": "Geography"
                },
                {
                    "id": 2,
                    "question": "Which planet is known as the Red Planet?",
                    "options": ["Venus", "Mars", "Jupiter", "Saturn"],
                    "answer": 1,  # Mars (0-based index)
                    "category": "Science"
                }
            ]
            save_questions(questions)
            return questions
    except Exception as e:
        logger.error(f"Error loading questions: {e}")
        return []

def save_questions(questions):
    """Save questions to the JSON file"""
    try:
        with open(QUESTIONS_FILE, 'w', encoding='utf-8') as file:
            json.dump(questions, file, ensure_ascii=False, indent=4)
        logger.info(f"Saved {len(questions)} questions")
        return True
    except Exception as e:
        logger.error(f"Error saving questions: {e}")
        return False

def get_next_question_id():
    """Get the next available question ID"""
    questions = load_questions()
    if not questions:
        return 1
    return max(q.get("id", 0) for q in questions) + 1

def get_question_by_id(question_id):
    """Get a question by its ID"""
    questions = load_questions()
    for question in questions:
        if question.get("id") == question_id:
            return question
    return None

def delete_question_by_id(question_id):
    """Delete a question by its ID"""
    questions = load_questions()
    updated_questions = [q for q in questions if q.get("id") != question_id]
    if len(updated_questions) < len(questions):
        save_questions(updated_questions)
        return True
    return False

def parse_telegram_quiz_url(url):
    """Parse a Telegram quiz URL to extract question and options"""
    try:
        # Basic URL validation
        if not url or "t.me" not in url:
            logger.error(f"Not a valid Telegram URL: {url}")
            return None
        
        # Try different methods to extract quiz content
        logger.info(f"Attempting to extract quiz from URL: {url}")
        
        # Method 1: Try to use Telegram API (Pyrogram) if credentials are available
        api_id = os.getenv('API_ID')
        api_hash = os.getenv('API_HASH')
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        
        if api_id and api_hash and bot_token:
            try:
                from pyrogram import Client
                import asyncio
                
                # Extract channel username and message ID from URL
                channel_pattern = r't\.me/([^/]+)/(\d+)'
                channel_match = re.search(channel_pattern, url)
                
                if channel_match:
                    channel_name = channel_match.group(1)
                    message_id = int(channel_match.group(2))
                    
                    # Function to get message using Pyrogram
                    async def get_quiz_message():
                        logger.info(f"Trying to fetch message from {channel_name}, ID: {message_id}")
                        async with Client(
                            "quiz_bot_client",
                            api_id=api_id,
                            api_hash=api_hash,
                            bot_token=bot_token,
                            in_memory=True
                        ) as app:
                            try:
                                message = await app.get_messages(channel_name, message_id)
                                if message:
                                    # If it's a poll message
                                    if message.poll:
                                        return {
                                            "question": message.poll.question,
                                            "options": [opt.text for opt in message.poll.options],
                                            "answer": 0  # Default, user will select correct answer
                                        }
                                    # If it's a text message that might contain quiz info
                                    elif message.text:
                                        # Try to parse text as quiz (question + options format)
                                        lines = message.text.strip().split('\n')
                                        if len(lines) >= 3:  # At least 1 question and 2 options
                                            question = lines[0]
                                            options = []
                                            
                                            # Extract options (look for numbered/lettered options)
                                            for line in lines[1:]:
                                                line = line.strip()
                                                # Remove common option prefixes
                                                line = re.sub(r'^[a-z][\.\)]\s*', '', line)
                                                line = re.sub(r'^\d+[\.\)]\s*', '', line)
                                                if line:
                                                    options.append(line)
                                            
                                            if len(options) >= 2:
                                                return {
                                                    "question": question,
                                                    "options": options,
                                                    "answer": 0
                                                }
                            except Exception as e:
                                logger.error(f"Error getting message with Pyrogram: {e}")
                                return None
                        return None
                    
                    # Run the async function
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result = loop.run_until_complete(get_quiz_message())
                    loop.close()
                    
                    if result:
                        logger.info(f"Successfully extracted quiz via Pyrogram: {result['question']}")
                        return result
            except Exception as e:
                logger.error(f"Pyrogram method failed: {e}")
        
        # Method 2: Enhanced web scraping with multiple patterns
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
        
        # Try to get both the regular URL and the embedded version
        try:
            response = requests.get(url, headers=headers)
            content = response.text
            
            # First, look for standard poll format
            poll_q_match = re.search(r'<div class="tgme_widget_message_poll_question">([^<]+)</div>', content)
            poll_options = re.findall(r'<div class="tgme_widget_message_poll_option_text">([^<]+)</div>', content)
            
            if poll_q_match and poll_options and len(poll_options) >= 2:
                question = poll_q_match.group(1).strip()
                return {
                    "question": question,
                    "options": poll_options,
                    "answer": 0
                }
            
            # If not a direct poll, try embedded view
            if "rajsthangk" in url or "gk" in url.lower() or "quiz" in url.lower():
                # Try to extract channel and message_id
                channel_pattern = r't\.me/([^/]+)/(\d+)'
                channel_match = re.search(channel_pattern, url)
                
                if channel_match:
                    channel_name = channel_match.group(1)
                    message_id = channel_match.group(2)
                    
                    # Try embedded view
                    embed_url = f"https://t.me/{channel_name}/{message_id}?embed=1"
                    try:
                        embed_response = requests.get(embed_url, headers=headers)
                        embed_content = embed_response.text
                        
                        # Try to find quiz in embedded view
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(embed_content, 'html.parser')
                        
                        # Look for message text that might contain quiz
                        message_text = soup.select_one('.tgme_widget_message_text')
                        if message_text:
                            text = message_text.get_text().strip()
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            
                            if lines and len(lines) >= 3:  # At least question + 2 options
                                question = lines[0]
                                
                                # Check if this looks like a quiz (has options with A), B), 1., 2., etc.)
                                option_pattern = re.compile(r'^[A-Za-z0-9][\.\)]')
                                options = []
                                for line in lines[1:]:
                                    # Remove option markers
                                    clean_line = re.sub(r'^[A-Za-z0-9][\.\)]\s*', '', line)
                                    if clean_line:
                                        options.append(clean_line)
                                
                                if len(options) >= 2:
                                    logger.info(f"Extracted quiz from message text with {len(options)} options")
                                    return {
                                        "question": question,
                                        "options": options,
                                        "answer": 0
                                    }
                        
                        # For RAJ GK QUIZ HOUSE format, look for quiz title
                        page_title = soup.select_one('meta[property="og:title"]')
                        if page_title and "quiz" in page_title.get('content', '').lower():
                            title = page_title.get('content', '').strip()
                            
                            # Try to extract options from the page
                            lines = []
                            for p in soup.select('.tgme_widget_message_text p'):
                                lines.append(p.get_text().strip())
                            
                            # If we have potential options
                            if lines and len(lines) >= 2:
                                return {
                                    "question": title,
                                    "options": lines,
                                    "answer": 0
                                }
                    except Exception as e:
                        logger.error(f"Error processing embedded view: {e}")
            
            # Method 3: Try to extract from general message content
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # Try to get title and description from meta tags
                title = None
                title_tag = soup.select_one('meta[property="og:title"]')
                if title_tag:
                    title = title_tag.get('content', '').strip()
                
                description = None
                desc_tag = soup.select_one('meta[property="og:description"]')
                if desc_tag:
                    description = desc_tag.get('content', '').strip()
                
                # If we have both title and description
                if title and description:
                    # Use title as question and try to extract options from description
                    if "quiz" in title.lower():
                        # Try to split description into options
                        options = [opt.strip() for opt in description.split(',') if opt.strip()]
                        if len(options) >= 2:
                            return {
                                "question": title,
                                "options": options,
                                "answer": 0
                            }
            except Exception as e:
                logger.error(f"Error parsing with BeautifulSoup: {e}")
        
        except Exception as e:
            logger.error(f"Error fetching or parsing URL: {e}")
        
        logger.warning(f"Could not extract quiz automatically from {url}")
        return None
        
    except Exception as e:
        logger.error(f"Error in parse_telegram_quiz_url: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def load_users():
    """Load users from the JSON file"""
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as file:
                return json.load(file)
        return {}
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        return {}

def save_user_data(user_data):
    """Save user data to the JSON file"""
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as file:
            json.dump(user_data, file, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving user data: {e}")
        return False

def update_user_stats(user_id, user_name, is_correct):
    """Update user statistics"""
    user_data = load_users()
    
    # Convert user_id to string for JSON compatibility
    user_id = str(user_id)
    
    if user_id not in user_data:
        user_data[user_id] = {
            "name": user_name,
            "correct": 0,
            "total": 0
        }
    
    user_data[user_id]["total"] += 1
    if is_correct:
        user_data[user_id]["correct"] += 1
    
    return save_user_data(user_data)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command"""
    user = update.effective_user
    await update.message.reply_text(
        f"Hello, {user.first_name}! I'm the Quiz Bot 🎯\n\n"
        f"I can help you play and create quiz questions.\n\n"
        f"Use /play to start a quiz\n"
        f"Use /add to create a new quiz question\n"
        f"Use /help to see all available commands"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /help command"""
    help_text = (
        "📚 *Available Commands* 📚\n\n"
        "/start - Start the bot\n"
        "/play - Play a random quiz\n"
        "/stats - View your quiz statistics\n"
        "/add - Create a new quiz question\n"
        "/list - List all available quizzes\n"
        "/clone - Clone a quiz from a Telegram link\n"
        "/edit - Edit an existing quiz\n"
        "/remove - Delete a quiz question\n"
        "/saveforward - Save a forwarded quiz\n"
        "/cancel - Cancel current operation\n"
        "/help - Show this help message\n\n"
        "To save a quiz someone sent you, forward it to me and I'll ask if you want to save it!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /stats command"""
    user = update.effective_user
    user_data = load_users()
    user_id = str(user.id)
    
    if user_id in user_data:
        stats = user_data[user_id]
        accuracy = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0
        
        await update.message.reply_text(
            f"📊 *Your Quiz Statistics* 📊\n\n"
            f"Total questions answered: {stats['total']}\n"
            f"Correct answers: {stats['correct']}\n"
            f"Accuracy: {accuracy:.1f}%",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "You haven't answered any quiz questions yet. Use /play to start a quiz!"
        )

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /play command - starts a quiz"""
    questions = load_questions()
    
    if not questions:
        await update.message.reply_text(
            "No quiz questions available. Use /add to create some!"
        )
        return
    
    # Select a random question
    question_data = random.choice(questions)
    question = question_data["question"]
    options = question_data["options"]
    correct_option_id = question_data["answer"]
    
    # Store the correct answer in user_data for checking later
    context.user_data["quiz_correct_answer"] = correct_option_id
    context.user_data["quiz_question_id"] = question_data.get("id")
    
    # Send the quiz as a poll
    message = await context.bot.send_poll(
        chat_id=update.effective_chat.id,
        question=question,
        options=options,
        type=Poll.QUIZ,
        correct_option_id=correct_option_id,
        explanation=f"This question is from category: {question_data.get('category', 'General')}",
        is_anonymous=False
    )
    
    # Store the poll message for reference
    context.user_data["quiz_message_id"] = message.message_id
    
    # Log that a quiz was started
    logger.info(f"Started quiz for user {update.effective_user.name}")

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for when a user answers a poll"""
    answer = update.poll_answer
    user_id = answer.user.id
    user_name = answer.user.name
    
    # Get the selected option
    selected_option = answer.option_ids[0] if answer.option_ids else None
    
    # Get the correct answer from user_data
    correct_option = context.user_data.get("quiz_correct_answer")
    
    # Check if the answer is correct
    is_correct = (selected_option == correct_option)
    
    # Update user stats
    update_user_stats(user_id, user_name, is_correct)
    
    # Send feedback message
    await context.bot.send_message(
        chat_id=user_id,
        text=f"{'✅ Correct!' if is_correct else '❌ Wrong!'} Use /play to try another quiz."
    )

async def add_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Command to start adding a new quiz question"""
    await update.message.reply_text(
        "Let's create a new quiz question.\n\n"
        "First, send me the question text.\n"
        "For example: 'What is the capital of France?'\n\n"
        "Type /cancel to abort."
    )
    return QUESTION

async def get_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiving the question text"""
    question_text = update.message.text
    context.user_data["quiz_question"] = question_text
    
    await update.message.reply_text(
        "Great! Now send me the answer options, one per line.\n"
        "For example:\n"
        "Paris\n"
        "London\n"
        "Berlin\n"
        "Rome\n\n"
        "Type /cancel to abort."
    )
    return OPTIONS

async def get_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiving the answer options"""
    options_text = update.message.text
    options = [opt.strip() for opt in options_text.split('\n') if opt.strip()]
    
    if len(options) < 2:
        await update.message.reply_text(
            "You need to provide at least 2 options. Please try again."
        )
        return OPTIONS
    
    context.user_data["quiz_options"] = options
    
    # Create option buttons for selecting the correct answer
    keyboard = []
    for i, option in enumerate(options):
        keyboard.append([InlineKeyboardButton(f"{i+1}. {option}", callback_data=f"answer_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Now select which option is the correct answer:",
        reply_markup=reply_markup
    )
    return ANSWER

async def get_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiving the correct answer"""
    query = update.callback_query
    await query.answer()
    
    # Extract the answer index from callback data
    answer_idx = int(query.data.split('_')[1])
    
    # Get the quiz data from user_data
    question = context.user_data.get("quiz_question")
    options = context.user_data.get("quiz_options")
    
    # Generate a new question ID
    question_id = get_next_question_id()
    
    # Create new question dictionary
    new_question = {
        "id": question_id,
        "question": question,
        "options": options,
        "answer": answer_idx,
        "category": "User Created"
    }
    
    # Add to questions file
    questions = load_questions()
    questions.append(new_question)
    save_questions(questions)
    
    # Notify the user
    await query.edit_message_text(
        f"✅ Quiz question created successfully!\n\n"
        f"Question: {question}\n"
        f"Correct answer: {options[answer_idx]}\n\n"
        f"Use /play to try it out or /add to create another."
    )
    
    # Clear user data
    context.user_data.clear()
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the quiz creation process"""
    await update.message.reply_text(
        "Quiz creation cancelled. Use /help to see available commands."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def list_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available quizzes"""
    questions = load_questions()
    
    if not questions:
        await update.message.reply_text("No quiz questions available. Use /add to create some!")
        return
    
    # Group questions by category
    categories = {}
    for q in questions:
        category = q.get("category", "General")
        if category not in categories:
            categories[category] = []
        categories[category].append(q)
    
    # Format the response message
    message = "📋 *Available Quiz Questions* 📋\n\n"
    
    for category, questions in categories.items():
        message += f"*{category}* ({len(questions)})\n"
        for q in questions[:5]:  # Show only first 5 questions per category
            message += f"- ID {q['id']}: {q['question'][:30]}...\n"
        if len(questions) > 5:
            message += f"  ... and {len(questions) - 5} more\n"
        message += "\n"
    
    message += "Use /play to play a random quiz, or /edit [ID] to edit a specific question."
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def clone_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clone a quiz from a Telegram link"""
    command_args = context.args
    
    if command_args and command_args[0].startswith('http'):
        # If a URL is provided directly with the command
        url = command_args[0]
        return await handle_quiz_url(update, context, url)
    else:
        # Otherwise, ask for the URL
        await update.message.reply_text(
            "Please send me the Telegram quiz link you want to clone.\n"
            "For example, a link from @QuizBot or another quiz bot or channel.\n\n"
            "Type /cancel to abort."
        )
        return CLONE_URL

async def handle_quiz_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url=None):
    """Handle processing a quiz URL"""
    if not url:
        url = update.message.text
    
    await update.message.reply_text("Analyzing the quiz link... Please wait.")
    
    # Parse the URL to extract quiz data
    quiz_data = parse_telegram_quiz_url(url)
    
    if not quiz_data:
        # If direct parsing failed, ask user to enter quiz details manually
        await update.message.reply_text(
            "I couldn't automatically extract the quiz from that link.\n\n"
            "Let's create it manually. Please send me the question text."
        )
        # Store URL for reference
        context.user_data["manual_clone_url"] = url
        return QUESTION
    
    # Store the parsed data
    context.user_data["quiz_question"] = quiz_data["question"]
    context.user_data["quiz_options"] = quiz_data["options"]
    
    # Create option buttons for selecting correct answer
    keyboard = []
    for i, option in enumerate(quiz_data["options"]):
        keyboard.append([InlineKeyboardButton(f"{i+1}. {option}", callback_data=f"answer_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Ask the user to select the correct answer
    await update.message.reply_text(
        f"I found the following quiz:\n\n"
        f"Question: {quiz_data['question']}\n\n"
        f"Options:\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(quiz_data["options"])) + "\n\n"
        f"Now select which option is the correct answer:",
        reply_markup=reply_markup
    )
    
    return ANSWER

async def handle_quiz_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url=None):
    """Handle processing a quiz URL"""
    if not url:
        url = update.message.text
    
    await update.message.reply_text("Analyzing the quiz link... Please wait.")
    
    # Parse the URL to extract quiz data
    quiz_data = parse_telegram_quiz_url(url)
    
    if not quiz_data:
        # If direct parsing failed, ask user to enter quiz details manually
        await update.message.reply_text(
            "I couldn't automatically extract the quiz from that link.\n\n"
            "Let's create it manually. Please send me the question text."
        )
        context.user_data["clone_url"] = url  # Store the URL for reference
        return QUESTION
    
    # Store the parsed data
    context.user_data["quiz_question"] = quiz_data["question"]
    context.user_data["quiz_options"] = quiz_data["options"]
    
    # Create option buttons for selecting correct answer
    keyboard = []
    for i, option in enumerate(quiz_data["options"]):
        keyboard.append([InlineKeyboardButton(f"{i+1}. {option}", callback_data=f"answer_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Ask the user to select the correct answer
    await update.message.reply_text(
        f"I found the following quiz:\n\n"
        f"Question: {quiz_data['question']}\n\n"
        f"Options:\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(quiz_data["options"])) + "\n\n"
        f"Now select which option is the correct answer:",
        reply_markup=reply_markup
    )
    
    return ANSWER

async def edit_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit an existing quiz"""
    command_args = context.args
    
    if not command_args:
        # Show list of quizzes for selection
        questions = load_questions()
        if not questions:
            await update.message.reply_text("No quiz questions available to edit.")
            return ConversationHandler.END
        
        keyboard = []
        # Show up to 10 questions for selection
        for q in questions[:10]:
            keyboard.append([InlineKeyboardButton(
                f"ID {q['id']}: {q['question'][:30]}...", 
                callback_data=f"edit_{q['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Select a quiz to edit:",
            reply_markup=reply_markup
        )
        return EDIT_SELECT
    else:
        # Try to get the question by ID
        try:
            question_id = int(command_args[0])
            question = get_question_by_id(question_id)
            
            if not question:
                await update.message.reply_text(f"No question found with ID {question_id}.")
                return ConversationHandler.END
            
            # Store the question for editing
            context.user_data["edit_question"] = question
            
            # Show edit options
            keyboard = [
                [InlineKeyboardButton("Edit Question Text", callback_data="edit_text")],
                [InlineKeyboardButton("Edit Options", callback_data="edit_options")],
                [InlineKeyboardButton("Change Correct Answer", callback_data="edit_answer")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"Editing Quiz ID {question_id}:\n\n"
                f"Question: {question['question']}\n\n"
                f"Options:\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(question["options"])) + "\n\n"
                f"Correct answer: {question['options'][question['answer']]}\n\n"
                f"What would you like to edit?",
                reply_markup=reply_markup
            )
            
            return EDIT_SELECT
            
        except (ValueError, IndexError):
            await update.message.reply_text(
                "Invalid question ID. Use /list to see available quizzes and their IDs."
            )
            return ConversationHandler.END

async def edit_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle editing the question text"""
    query = update.callback_query
    await query.answer()
    
    question = context.user_data.get("edit_question")
    if not question:
        await query.edit_message_text("Error: No question being edited.")
        return ConversationHandler.END
    
    await query.edit_message_text(
        f"Send me the new text for the question:\n\n"
        f"Current: {question['question']}\n\n"
        f"Type /cancel to abort."
    )
    
    return EDIT_QUESTION

async def edit_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle editing the options"""
    query = update.callback_query
    await query.answer()
    
    question = context.user_data.get("edit_question")
    if not question:
        await query.edit_message_text("Error: No question being edited.")
        return ConversationHandler.END
    
    current_options = "\n".join(question["options"])
    
    await query.edit_message_text(
        f"Send me the new options, one per line:\n\n"
        f"Current options:\n{current_options}\n\n"
        f"Type /cancel to abort."
    )
    
    return EDIT_OPTIONS

async def edit_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle setting the correct answer"""
    query = update.callback_query
    await query.answer()
    
    question = context.user_data.get("edit_question")
    if not question:
        await query.edit_message_text("Error: No question being edited.")
        return ConversationHandler.END
    
    # Create option buttons for selecting the correct answer
    keyboard = []
    for i, option in enumerate(question["options"]):
        keyboard.append([InlineKeyboardButton(f"{i+1}. {option}", callback_data=f"editanswer_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Select the new correct answer:\n\n"
        f"Current correct answer: {question['options'][question['answer']]}\n\n"
        f"Type /cancel to abort.",
        reply_markup=reply_markup
    )
    
    return EDIT_ANSWER

async def save_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a forwarded quiz from Telegram"""
    if not update.message.forward_from_message_id:
        await update.message.reply_text(
            "This command is for saving forwarded quiz polls.\n"
            "Forward a quiz poll to me and then use this command.\n\n"
            "Alternatively, you can use /add to create a quiz manually."
        )
        return
    
    # Check if this is a forwarded poll
    if update.message.poll:
        poll = update.message.poll
        
        # Check if it's a quiz poll
        if poll.type != Poll.QUIZ:
            await update.message.reply_text(
                "This appears to be a regular poll, not a quiz poll.\n"
                "I can only save quiz polls with a correct answer."
            )
            return
        
        # Extract question and options
        question_text = poll.question
        options = [opt.text for opt in poll.options]
        
        # Store for later processing
        context.user_data["quiz_question"] = question_text
        context.user_data["quiz_options"] = options
        
        # Create option buttons for selecting the correct answer
        keyboard = []
        for i, option in enumerate(options):
            keyboard.append([InlineKeyboardButton(f"{i+1}. {option}", callback_data=f"answer_{i}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"I found this quiz poll:\n\n"
            f"Question: {question_text}\n\n"
            f"Options:\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options)) + "\n\n"
            f"Which option is the correct answer?",
            reply_markup=reply_markup
        )
        
        return ANSWER
    else:
        await update.message.reply_text(
            "The forwarded message doesn't appear to contain a quiz poll.\n"
            "Try forwarding a quiz poll from another bot like @QuizBot."
        )
        return ConversationHandler.END

async def remove_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a quiz by ID"""
    command_args = context.args
    
    if not command_args:
        # Show list of quizzes for selection
        questions = load_questions()
        if not questions:
            await update.message.reply_text("No quiz questions available to remove.")
            return
        
        keyboard = []
        # Show up to 10 questions for selection
        for q in questions[:10]:
            keyboard.append([InlineKeyboardButton(
                f"ID {q['id']}: {q['question'][:30]}...", 
                callback_data=f"remove_{q['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Select a quiz to remove:",
            reply_markup=reply_markup
        )
    else:
        # Try to get the question by ID
        try:
            question_id = int(command_args[0])
            question = get_question_by_id(question_id)
            
            if not question:
                await update.message.reply_text(f"No question found with ID {question_id}.")
                return
            
            # Create confirm/cancel buttons
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, delete it", callback_data=f"confirm_remove_{question_id}"),
                    InlineKeyboardButton("❌ No, keep it", callback_data="cancel_remove")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"Are you sure you want to delete this quiz?\n\n"
                f"ID: {question_id}\n"
                f"Question: {question['question']}\n"
                f"Category: {question.get('category', 'General')}",
                reply_markup=reply_markup
            )
            
        except (ValueError, IndexError):
            await update.message.reply_text(
                "Invalid question ID. Use /list to see available quizzes and their IDs."
            )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks for quiz deletion confirmation"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    
    if callback_data == "cancel_remove":
        await query.edit_message_text("Quiz deletion cancelled.")
        return
    
    elif callback_data.startswith("confirm_remove_"):
        try:
            question_id = int(callback_data.split("_")[2])
            question = get_question_by_id(question_id)
            
            if not question:
                await query.edit_message_text(f"Error: Question ID {question_id} not found.")
                return
            
            # Delete the question
            if delete_question_by_id(question_id):
                await query.edit_message_text(f"✅ Quiz question ID {question_id} has been deleted.")
            else:
                await query.edit_message_text(f"❌ Failed to delete question ID {question_id}.")
        
        except (ValueError, IndexError) as e:
            await query.edit_message_text(f"Error processing request: {e}")
    
    elif callback_data.startswith("remove_"):
        try:
            question_id = int(callback_data.split("_")[1])
            question = get_question_by_id(question_id)
            
            if not question:
                await query.edit_message_text(f"Error: Question ID {question_id} not found.")
                return
            
            # Create confirm/cancel buttons
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, delete it", callback_data=f"confirm_remove_{question_id}"),
                    InlineKeyboardButton("❌ No, keep it", callback_data="cancel_remove")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"Are you sure you want to delete this quiz?\n\n"
                f"ID: {question_id}\n"
                f"Question: {question['question']}\n"
                f"Category: {question.get('category', 'General')}",
                reply_markup=reply_markup
            )
            
        except (ValueError, IndexError) as e:
            await query.edit_message_text(f"Error processing request: {e}")

    elif callback_data.startswith("edit_"):
        if callback_data == "edit_text":
            await edit_question_text(update, context)
        elif callback_data == "edit_options":
            await edit_options(update, context)
        elif callback_data == "edit_answer":
            await edit_answer(update, context)
        else:
            try:
                # Handle selecting a question to edit
                question_id = int(callback_data.split("_")[1])
                question = get_question_by_id(question_id)
                
                if not question:
                    await query.edit_message_text(f"Error: Question ID {question_id} not found.")
                    return
                
                # Store the question for editing
                context.user_data["edit_question"] = question
                
                # Show edit options
                keyboard = [
                    [InlineKeyboardButton("Edit Question Text", callback_data="edit_text")],
                    [InlineKeyboardButton("Edit Options", callback_data="edit_options")],
                    [InlineKeyboardButton("Change Correct Answer", callback_data="edit_answer")]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"Editing Quiz ID {question_id}:\n\n"
                    f"Question: {question['question']}\n\n"
                    f"Options:\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(question["options"])) + "\n\n"
                    f"Correct answer: {question['options'][question['answer']]}\n\n"
                    f"What would you like to edit?",
                    reply_markup=reply_markup
                )
            
            except (ValueError, IndexError) as e:
                await query.edit_message_text(f"Error processing request: {e}")
    
    elif callback_data.startswith("editanswer_"):
        try:
            # Extract the answer index from callback data
            answer_idx = int(callback_data.split('_')[1])
            
            # Get the question being edited
            question = context.user_data.get("edit_question")
            if not question:
                await query.edit_message_text("Error: No question being edited.")
                return ConversationHandler.END
            
            # Update the correct answer
            question["answer"] = answer_idx
            
            # Save the changes
            questions = load_questions()
            for i, q in enumerate(questions):
                if q.get("id") == question.get("id"):
                    questions[i] = question
                    break
            
            save_questions(questions)
            
            # Confirm the changes
            await query.edit_message_text(
                f"✅ Quiz updated successfully!\n\n"
                f"Question: {question['question']}\n"
                f"New correct answer: {question['options'][answer_idx]}\n\n"
                f"Use /list to see all quizzes or /play to try one."
            )
            
            # Clear user data
            context.user_data.clear()
            
            return ConversationHandler.END
            
        except (ValueError, IndexError) as e:
            await query.edit_message_text(f"Error updating answer: {e}")
            return ConversationHandler.END

def main():
    """Start the bot"""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("play", play))
    application.add_handler(CommandHandler("list", list_quizzes))
    application.add_handler(CommandHandler("remove", remove_quiz))
    
    # Handle poll answers
    application.add_handler(PollHandler(handle_poll_answer))
    
    # Add conversation handler for quiz creation
    add_quiz_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_quiz)],
        states={
            QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_question)],
            OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_options)],
            ANSWER: [CallbackQueryHandler(get_answer, pattern=r"^answer_")]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_quiz_conv)
    
    # Add conversation handler for quiz cloning
    clone_quiz_conv = ConversationHandler(
        entry_points=[CommandHandler("clone", clone_quiz)],
        states={
            CLONE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quiz_url)],
            ANSWER: [CallbackQueryHandler(get_answer, pattern=r"^answer_")]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(clone_quiz_conv)
    
    # Add conversation handler for poll-to-quiz conversion editing
    edit_poll_quiz_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_edit_selection, pattern=r"^edit_")],
        states={
            EDIT_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_text)],
            EDIT_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_options)],
            EDIT_ANSWER: [CallbackQueryHandler(handle_edit_answer, pattern=r"^editanswer_")]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(edit_poll_quiz_conv)
    
    # Handle poll to quiz conversion
    application.add_handler(CallbackQueryHandler(handle_poll_to_quiz, pattern=r"^polltoquiz_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: None, pattern=r"^test_quiz_"))  # Placeholder for test_quiz handler
    
    # Add handler for general message handling (including forwarded polls)
    application.add_handler(MessageHandler(
        filters.FORWARDED & filters.POLL | filters.TEXT & ~filters.COMMAND, 
        handle_message
    ))
    
    # Add callback query handler for button callbacks
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the Bot
    application.run_polling()

# Constants for conversation states (if they're not already defined)
EDIT_QUESTION, EDIT_OPTIONS, EDIT_ANSWER = range(5, 8)

# Function to get the next available question ID
def get_next_question_id():
    questions = load_questions()
    if not questions:
        return 1
    return max(q.get("id", 0) for q in questions) + 1

# Function to get a question by ID
def get_question_by_id(question_id):
    questions = load_questions()
    for question in questions:
        if question.get("id") == question_id:
            return question
    return None

# Handle forwarded polls conversion to quiz questions
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages sent to the bot"""
    message = update.message
    
    # Check if the message is a forwarded poll
    if message.forward_date and message.poll:
        poll = message.poll
        
        # Extract poll information
        question_text = poll.question
        options = [option.text for option in poll.options]
        
        # Create keyboard to select the correct answer
        keyboard = []
        for i, option in enumerate(options):
            keyboard.append([InlineKeyboardButton(
                f"{i+1}. {option}", callback_data=f"polltoquiz_{i}"
            )])
        
        # Store poll info in context.user_data
        context.user_data["poll_to_quiz"] = {
            "question": question_text,
            "options": options
        }
        
        # Ask user to select the correct answer
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "📝 I received a poll! I'll convert it to a quiz question.\n\n"
            f"Question: {question_text}\n\n"
            "Please select the correct answer:",
            reply_markup=reply_markup
        )
    else:
        # Regular message handling
        await message.reply_text(
            "I can help you manage quiz questions. Try /help to see available commands, "
            "or forward me a poll to convert it to a quiz question!"
        )

async def handle_poll_to_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle selection of correct answer for poll to quiz conversion"""
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("polltoquiz_"):
        return
    
    # Get the selected answer index
    option_id = int(query.data.split("_")[1])
    
    # Get poll data from user_data
    poll_data = context.user_data.get("poll_to_quiz")
    if not poll_data:
        await query.edit_message_text("Sorry, I couldn't find the poll data. Please try again.")
        return
    
    # Create new question
    question_id = get_next_question_id()
    new_question = {
        "id": question_id,
        "question": poll_data["question"],
        "options": poll_data["options"],
        "answer": option_id,  # Using the selected option as correct answer
        "category": "Converted Poll"
    }
    
    # Add question to database
    questions = load_questions()
    questions.append(new_question)
    save_questions(questions)
    
    # Create a preview of the quiz
    preview = f"✅ Quiz added successfully!\n\nID: {question_id}\n"
    preview += f"Question: {new_question['question']}\n\nOptions:\n"
    
    for i, option in enumerate(new_question['options']):
        correct_mark = " ✓" if i == option_id else ""
        preview += f"{i+1}. {option}{correct_mark}\n"
    
    # Provide edit options
    keyboard = [
        [InlineKeyboardButton("Edit Question", callback_data=f"edit_question_{question_id}")],
        [InlineKeyboardButton("Edit Options", callback_data=f"edit_options_{question_id}")],
        [InlineKeyboardButton("Change Answer", callback_data=f"edit_answer_{question_id}")],
        [InlineKeyboardButton("Test this Quiz", callback_data=f"test_quiz_{question_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Clean up user_data
    context.user_data.pop("poll_to_quiz", None)
    
    await query.edit_message_text(preview, reply_markup=reply_markup)

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit selections for converted polls"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    if len(parts) < 3:
        return
    
    action = parts[1]
    try:
        question_id = int(parts[2])
    except ValueError:
        await query.edit_message_text("Invalid question ID.")
        return
    
    question = get_question_by_id(question_id)
    if not question:
        await query.edit_message_text("Question not found.")
        return
    
    if action == "question":
        # Edit question text
        context.user_data["editing"] = {
            "question_id": question_id,
            "action": "question"
        }
        await query.edit_message_text(
            f"Current question: {question['question']}\n\n"
            "Please send me the new question text:"
        )
        return EDIT_QUESTION
    
    elif action == "options":
        # Edit options
        context.user_data["editing"] = {
            "question_id": question_id,
            "action": "options"
        }
        options_text = "\n".join(question["options"])
        await query.edit_message_text(
            f"Current options:\n{options_text}\n\n"
            "Please send me the new options, one per line:"
        )
        return EDIT_OPTIONS
    
    elif action == "answer":
        # Change correct answer
        context.user_data["editing"] = {
            "question_id": question_id,
            "action": "answer"
        }
        
        # Create keyboard with options
        keyboard = []
        for i, option in enumerate(question["options"]):
            correct_mark = " ✓" if i == question["answer"] else ""
            keyboard.append([InlineKeyboardButton(
                f"{i+1}. {option}{correct_mark}", 
                callback_data=f"editanswer_{question_id}_{i}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Question: {question['question']}\n\n"
            "Select the correct answer:",
            reply_markup=reply_markup
        )
        return EDIT_ANSWER
    
    elif action == "test":
        # Test the quiz
        question_text = question["question"]
        options = question["options"]
        correct_option = question["answer"]
        
        # Send as quiz poll
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question_text,
            options=options,
            type=Poll.QUIZ,
            correct_option_id=correct_option,
            explanation="Test quiz from your converted poll"
        )
        
        await query.edit_message_text(
            f"I've sent the quiz for testing. If you need to make more changes, use /edit {question_id}"
        )

async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle editing the question text"""
    new_text = update.message.text
    editing_data = context.user_data.get("editing")
    
    if not editing_data:
        await update.message.reply_text("Error: No editing session in progress. Please try again.")
        return ConversationHandler.END
    
    question_id = editing_data.get("question_id")
    question = get_question_by_id(question_id)
    
    if not question:
        await update.message.reply_text("Error: Question not found.")
        return ConversationHandler.END
    
    # Update the question text
    questions = load_questions()
    for q in questions:
        if q.get("id") == question_id:
            q["question"] = new_text
            break
    
    save_questions(questions)
    
    # Clear editing data
    context.user_data.pop("editing", None)
    
    await update.message.reply_text(
        f"✅ Question updated successfully!\n\n"
        f"New question: {new_text}\n\n"
        f"Use /play to test it or /edit {question_id} to make more changes."
    )
    
    return ConversationHandler.END

async def handle_edit_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle editing the question options"""
    new_options_text = update.message.text
    new_options = [opt.strip() for opt in new_options_text.split('\n') if opt.strip()]
    
    if len(new_options) < 2:
        await update.message.reply_text(
            "You need to provide at least 2 options. Please try again, with one option per line."
        )
        return EDIT_OPTIONS
    
    editing_data = context.user_data.get("editing")
    
    if not editing_data:
        await update.message.reply_text("Error: No editing session in progress. Please try again.")
        return ConversationHandler.END
    
    question_id = editing_data.get("question_id")
    question = get_question_by_id(question_id)
    
    if not question:
        await update.message.reply_text("Error: Question not found.")
        return ConversationHandler.END
    
    # Update the options
    old_answer = question["answer"]
    questions = load_questions()
    
    for q in questions:
        if q.get("id") == question_id:
            q["options"] = new_options
            # Ensure the answer is still valid
            if old_answer >= len(new_options):
                q["answer"] = 0  # Default to first option if old answer is invalid
            break
    
    save_questions(questions)
    
    # Clear editing data
    context.user_data.pop("editing", None)
    
    # Show updated options with answer marked
    updated_question = get_question_by_id(question_id)
    options_display = ""
    for i, option in enumerate(updated_question["options"]):
        correct_mark = " ✓" if i == updated_question["answer"] else ""
        options_display += f"{i+1}. {option}{correct_mark}\n"
    
    await update.message.reply_text(
        f"✅ Options updated successfully!\n\n"
        f"Question: {updated_question['question']}\n\n"
        f"New options:\n{options_display}\n"
        f"Use /play to test it or /edit {question_id} to make more changes."
    )
    
    return ConversationHandler.END

async def handle_edit_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle changing the correct answer"""
    query = update.callback_query
    await query.answer()
    
    # Format: editanswer_questionid_optionid
    parts = query.data.split('_')
    if len(parts) < 3:
        await query.edit_message_text("Invalid selection. Please try again.")
        return ConversationHandler.END
    
    try:
        question_id = int(parts[1])
        new_answer = int(parts[2])
    except ValueError:
        await query.edit_message_text("Invalid selection format. Please try again.")
        return ConversationHandler.END
    
    # Update the correct answer
    questions = load_questions()
    for q in questions:
        if q.get("id") == question_id:
            q["answer"] = new_answer
            break
    
    save_questions(questions)
    
    # Clear editing data
    context.user_data.pop("editing", None)
    
    # Show updated question with new answer marked
    updated_question = get_question_by_id(question_id)
    options_display = ""
    for i, option in enumerate(updated_question["options"]):
        correct_mark = " ✓" if i == new_answer else ""
        options_display += f"{i+1}. {option}{correct_mark}\n"
    
    await query.edit_message_text(
        f"✅ Correct answer updated!\n\n"
        f"Question: {updated_question['question']}\n\n"
        f"Options:\n{options_display}\n"
        f"Use /play to test it or /edit {question_id} to make more changes."
    )
    
    return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages sent to the bot"""
    message = update.message
    
    # Check if the message is a forwarded poll
    if message.forward_date and message.poll:
        poll = message.poll
        
        # Extract poll information
        question_text = poll.question
        options = [option.text for option in poll.options]
        
        # Create keyboard to select the correct answer
        keyboard = []
        for i, option in enumerate(options):
            keyboard.append([InlineKeyboardButton(
                f"{i+1}. {option}", callback_data=f"polltoquiz_{i}"
            )])
        
        # Store poll info in context.user_data
        context.user_data["poll_to_quiz"] = {
            "question": question_text,
            "options": options
        }
        
        # Ask user to select the correct answer
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "📝 I received a poll! I'll convert it to a quiz question.\n\n"
            f"Question: {question_text}\n\n"
            "Please select the correct answer:",
            reply_markup=reply_markup
        )
    else:
        # Regular message handling
        await message.reply_text(
            "I can help you manage quiz questions. Try /help to see available commands, "
            "or forward me a poll to convert it to a quiz question!"
        )

async def handle_poll_to_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle selection of correct answer for poll to quiz conversion"""
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("polltoquiz_"):
        return
    
    # Get the selected answer index
    option_id = int(query.data.split("_")[1])
    
    # Get poll data from user_data
    poll_data = context.user_data.get("poll_to_quiz")
    if not poll_data:
        await query.edit_message_text("Sorry, I couldn't find the poll data. Please try again.")
        return
    
    # Create new question
    question_id = get_next_question_id()
    new_question = {
        "id": question_id,
        "question": poll_data["question"],
        "options": poll_data["options"],
        "answer": option_id,  # Using the selected option as correct answer
        "category": "Converted Poll"
    }
    
    # Add question to database
    questions = load_questions()
    questions.append(new_question)
    save_questions(questions)
    
    # Create a preview of the quiz
    preview = f"✅ Quiz added successfully!\n\nID: {question_id}\n"
    preview += f"Question: {new_question['question']}\n\nOptions:\n"
    
    for i, option in enumerate(new_question['options']):
        correct_mark = " ✓" if i == option_id else ""
        preview += f"{i+1}. {option}{correct_mark}\n"
    
    # Provide edit options
    keyboard = [
        [InlineKeyboardButton("Edit Question", callback_data=f"edit_question_{question_id}")],
        [InlineKeyboardButton("Edit Options", callback_data=f"edit_options_{question_id}")],
        [InlineKeyboardButton("Change Answer", callback_data=f"edit_answer_{question_id}")],
        [InlineKeyboardButton("Test this Quiz", callback_data=f"test_quiz_{question_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Clean up user_data
    context.user_data.pop("poll_to_quiz", None)
    
    await query.edit_message_text(preview, reply_markup=reply_markup)

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit selections for converted polls"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    if len(parts) < 3:
        return
    
    action = parts[1]
    try:
        question_id = int(parts[2])
    except ValueError:
        await query.edit_message_text("Invalid question ID.")
        return
    
    question = get_question_by_id(question_id)
    if not question:
        await query.edit_message_text("Question not found.")
        return
    
    if action == "question":
        # Edit question text
        context.user_data["editing"] = {
            "question_id": question_id,
            "action": "question"
        }
        await query.edit_message_text(
            f"Current question: {question['question']}\n\n"
            "Please send me the new question text:"
        )
        return EDIT_QUESTION
    
    elif action == "options":
        # Edit options
        context.user_data["editing"] = {
            "question_id": question_id,
            "action": "options"
        }
        options_text = "\n".join(question["options"])
        await query.edit_message_text(
            f"Current options:\n{options_text}\n\n"
            "Please send me the new options, one per line:"
        )
        return EDIT_OPTIONS
    
    elif action == "answer":
        # Change correct answer
        context.user_data["editing"] = {
            "question_id": question_id,
            "action": "answer"
        }
        
        # Create keyboard with options
        keyboard = []
        for i, option in enumerate(question["options"]):
            correct_mark = " ✓" if i == question["answer"] else ""
            keyboard.append([InlineKeyboardButton(
                f"{i+1}. {option}{correct_mark}", 
                callback_data=f"editanswer_{question_id}_{i}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Question: {question['question']}\n\n"
            "Select the correct answer:",
            reply_markup=reply_markup
        )
        return EDIT_ANSWER
    
    elif action == "test":
        # Test the quiz
        question_text = question["question"]
        options = question["options"]
        correct_option = question["answer"]
        
        # Send as quiz poll
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question_text,
            options=options,
            type=Poll.QUIZ,
            correct_option_id=correct_option,
            explanation="Test quiz from your converted poll"
        )
        
        await query.edit_message_text(
            f"I've sent the quiz for testing. If you need to make more changes, use /edit {question_id}"
        )



