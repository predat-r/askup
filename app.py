from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from pymongo import MongoClient, monitoring
from bson.objectid import ObjectId
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import logging
from functools import wraps
import warnings

# Disable PyMongo debug logs
logging.getLogger('pymongo').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
warnings.filterwarnings("ignore", category=UserWarning, module='pymongo')

# Disable command monitoring logs
monitoring._COMMAND_LOGGER = None
monitoring._SERVER_LOGGER = None
monitoring._TOPOLOGY_LOGGER = None
monitoring._CONNECTION_LOGGER = None

app = Flask(__name__)
app.secret_key =  os.getenv('SECRET_KEY')  
MONGO_URL = os.getenv('MONGO_URL')
try:
    client = MongoClient(MONGO_URL)
    db = client.askup
    users_collection = db.users
    questions_collection = db.questions
    answers_collection = db.answers
except Exception as e:
    print(f"Database connection error: {e}")

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Helper function to get current user
def get_current_user():
    if 'user_id' in session:
        return users_collection.find_one({'_id': ObjectId(session['user_id'])})
    return None

@app.route('/')
def index():
    questions = list(questions_collection.find().sort('created_at', -1).limit(20))
    
    # Get answer counts for each question
    for question in questions:
        question['answer_count'] = answers_collection.count_documents({'question_id': question['_id']})
        # Get user info
        user = users_collection.find_one({'_id': ObjectId(question['user_id'])})
        question['username'] = user['username'] if user else 'Anonymous'
    
    return render_template('index.html', questions=questions, current_user=get_current_user())

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        # Check if user already exists
        if users_collection.find_one({'$or': [{'username': username}, {'email': email}]}):
            flash('Username or email already exists')
            return redirect(url_for('register'))
        
        # Create new user
        hashed_password = generate_password_hash(password)
        user_id = users_collection.insert_one({
            'username': username,
            'email': email,
            'password': hashed_password,
            'created_at': datetime.utcnow()
        }).inserted_id
        
        session['user_id'] = str(user_id)
        flash('Registration successful!')
        return redirect(url_for('index'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = users_collection.find_one({'username': username})
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            flash('Login successful!')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('You have been logged out')
    return redirect(url_for('index'))

@app.route('/ask', methods=['GET', 'POST'])
@login_required
def ask_question():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        tags = [tag.strip() for tag in request.form['tags'].split(',') if tag.strip()]
        
        questions_collection.insert_one({
            'title': title,
            'content': content,
            'tags': tags,
            'user_id': ObjectId(session['user_id']),
            'created_at': datetime.utcnow(),
            'votes': 0,
            'voted_by': []
        })
        
        flash('Question posted successfully!')
        return redirect(url_for('index'))
    
    return render_template('ask.html', current_user=get_current_user())

import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@app.route('/question/<question_id>')
def view_question(question_id):
    if not question_id.strip():
        flash('Question ID is required')
        return redirect(url_for('index'))

    if not ObjectId.is_valid(question_id):
        flash('Invalid question ID')
        return redirect(url_for('index'))

    try:
        obj_id = ObjectId(question_id)
        question = questions_collection.find_one({'_id': obj_id})

        if not question:
            flash('Question not found')
            return redirect(url_for('index'))

        # Attach question author
        user_id = question.get('user_id')
        if user_id and ObjectId.is_valid(user_id):
            author = users_collection.find_one({'_id': ObjectId(user_id)})
            question['username'] = author['username'] if author else 'Anonymous'
        else:
            question['username'] = 'Anonymous'

        # Fetch answers and authors
        answers = list(answers_collection.find({'question_id': obj_id}).sort('votes', -1))
        for answer in answers:
            user_id = answer.get('user_id')
            if user_id and ObjectId.is_valid(user_id):
                author = users_collection.find_one({'_id': ObjectId(user_id)})
                answer['username'] = author['username'] if author else 'Anonymous'
            else:
                answer['username'] = 'Anonymous'

        return render_template('question.html', 
                               question=question, 
                               answers=answers, 
                               current_user=get_current_user())

    except Exception as e:
        app.logger.exception("Error in view_question")
        flash('An error occurred while loading the question')
        return redirect(url_for('index'))
    
@app.route('/answer/<question_id>', methods=['POST'])
@login_required
def post_answer(question_id):
    content = request.form['content']
    
    try:
        answers_collection.insert_one({
            'content': content,
            'question_id': ObjectId(question_id),
            'user_id': ObjectId(session['user_id']),
            'created_at': datetime.utcnow(),
            'votes': 0,
            'voted_by': []
        })
        
        flash('Answer posted successfully!')
    except Exception as e:
        flash('Error posting answer')
    
    return redirect(url_for('view_question', question_id=question_id))

@app.route('/vote/<item_type>/<item_id>/<vote_type>')
@login_required
def vote(item_type, item_id, vote_type):
    try:
        collection = questions_collection if item_type == 'question' else answers_collection
        item = collection.find_one({'_id': ObjectId(item_id)})
        
        if not item:
            return jsonify({'error': 'Item not found'}), 404
        
        user_id = ObjectId(session['user_id'])
        voted_by = item.get('voted_by', [])
        current_votes = item.get('votes', 0)
        
        # Check if user already voted
        user_vote = next((vote for vote in voted_by if vote['user_id'] == user_id), None)
        
        if user_vote:
            # User already voted, update or remove vote
            if user_vote['type'] == vote_type:
                # Same vote type, remove vote
                voted_by = [vote for vote in voted_by if vote['user_id'] != user_id]
                current_votes -= 1 if vote_type == 'up' else -1
            else:
                # Different vote type, change vote
                user_vote['type'] = vote_type
                current_votes += 2 if vote_type == 'up' else -2
        else:
            # New vote
            voted_by.append({'user_id': user_id, 'type': vote_type})
            current_votes += 1 if vote_type == 'up' else -1
        
        collection.update_one(
            {'_id': ObjectId(item_id)},
            {'$set': {'votes': current_votes, 'voted_by': voted_by}}
        )
        
        return jsonify({'votes': current_votes})
    
    except Exception as e:
        return jsonify({'error': 'Voting failed'}), 500

@app.route('/profile/<username>')
def profile(username):
    user = users_collection.find_one({'username': username})
    if not user:
        flash('User not found')
        return redirect(url_for('index'))
    
    user_questions = list(questions_collection.find({'user_id': user['_id']}).sort('created_at', -1))
    user_answers = list(answers_collection.find({'user_id': user['_id']}).sort('created_at', -1))
    
    # Get question titles for answers
    for answer in user_answers:
        question = questions_collection.find_one({'_id': answer['question_id']})
        answer['question_title'] = question['title'] if question else 'Unknown Question'
    
    return render_template('profile.html', 
                         user=user, 
                         questions=user_questions, 
                         answers=user_answers, 
                         current_user=get_current_user())

@app.route('/search')
def search():
    query = request.args.get('q', '')
    if query:
        # Simple text search in questions
        questions = list(questions_collection.find({
            '$or': [
                {'title': {'$regex': query, '$options': 'i'}},
                {'content': {'$regex': query, '$options': 'i'}},
                {'tags': {'$in': [query]}}
            ]
        }).sort('created_at', -1))
        
        # Get answer counts and usernames
        for question in questions:
            question['answer_count'] = answers_collection.count_documents({'question_id': question['_id']})
            user = users_collection.find_one({'_id': ObjectId(question['user_id'])})
            question['username'] = user['username'] if user else 'Anonymous'
    else:
        questions = []
    
    return render_template('search.html', questions=questions, query=query, current_user=get_current_user())

if __name__ == '__main__':
    app.run(debug=True)