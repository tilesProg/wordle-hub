# app.py
import os
import secrets
from datetime import datetime
from functools import wraps

from flask import (Flask, g, redirect, render_template, request, session,
                   url_for, flash, jsonify)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or secrets.token_hex(16)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'db.sqlite3')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Add custom Jinja filter
@app.template_filter('deserialize_cat_values')
def filter_deserialize_cat_values(text):
    return deserialize_cat_values(text)

# ---------- MODELS ----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    games = db.relationship('Game', backref='creator', lazy=True)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, default='')
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_hidden = db.Column(db.Boolean, default=False)       # hide from hub
    access = db.Column(db.String(20), default='public')    # public, link, private
    link_token = db.Column(db.String(64), unique=True, nullable=True)
    max_guesses = db.Column(db.Integer, nullable=True)     # optional limit
    categories = db.relationship('Category', backref='game', lazy=True, order_by="Category.position")
    words = db.relationship('WordEntry', backref='game', lazy=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(10), nullable=False)  # 'list', 'date', or 'number'
    position = db.Column(db.Integer, nullable=False)
    list_items = db.relationship('ListItem', backref='category', lazy=True)

class ListItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    value = db.Column(db.String(200), nullable=False)

class WordEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)   # a label for the word, used in guess selection
    # category values stored in a simple JSON-ish text column for portability
    # we'll store as "catid:value1|value2;;catid:value" — simple serialization
    cat_values = db.Column(db.Text, default='')  # serialized category values
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ---------- DB helpers ----------
def serialize_cat_values(mapping):
    # mapping: {category_id: [values] or "YYYY-MM-DD"}
    parts = []
    for cid, val in mapping.items():
        if isinstance(val, list):
            parts.append(f"{cid}:" + "|".join(val))
        else:
            parts.append(f"{cid}:{val}")
    return ";;".join(parts)

def deserialize_cat_values(text):
    out = {}
    if not text:
        return out
    for part in text.split(";;"):
        if ":" not in part:
            continue
        cid_s, vals = part.split(":", 1)
        if "|" in vals:
            out[int(cid_s)] = vals.split("|")
        else:
            out[int(cid_s)] = vals
    return out

# ---------- AUTH ----------
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return wrapped

@app.before_request
def load_user():
    g.user = None
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])

# ---------- ROUTES ----------
@app.route('/')
def index():
    return redirect(url_for('hub'))

@app.route('/hub')
def hub():
    games = Game.query.order_by(Game.id.desc()).all()
    visible = []
    token = request.args.get('token')
    for gme in games:
        if gme.is_hidden:
            # hidden: show only to creator or if link token matches or access settings allow
            if g.user and g.user.id == gme.creator_id:
                visible.append(gme)
            elif gme.access == 'link' and token and token == gme.link_token:
                visible.append(gme)
            # else skip
        else:
            if gme.access == 'private' and not (g.user and g.user.id == gme.creator_id):
                continue
            visible.append(gme)
    return render_template('hub.html', games=visible)

# --- Register / Login ---
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash('Provide username and password', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Username exists', 'danger')
            return redirect(url_for('register'))
        u = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(u)
        db.session.commit()
        session['user_id'] = u.id
        return redirect(url_for('hub'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        u = User.query.filter_by(username=username).first()
        if not u or not u.check_password(password):
            flash('Invalid credentials', 'danger')
            return redirect(url_for('login'))
        session['user_id'] = u.id
        return redirect(url_for('hub'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('hub'))

# --- Create game (constructor wizard multipart handled client-side) ---
@app.route('/game/create', methods=['GET','POST'])
@login_required
def create_game():
    if request.method == 'POST':
        data = request.json
        name = data.get('name','').strip()
        description = data.get('description','').strip()
        categories = data.get('categories', [])  # list of {name, kind}
        max_guesses = data.get('max_guesses') or None
        access = data.get('access','public')
        if not name or not categories:
            return jsonify({"ok":False, "error":"name and categories required"}), 400
        game = Game(name=name, description=description, creator_id=g.user.id, access=access)
        if access == 'link':
            game.link_token = secrets.token_hex(16)
        if max_guesses:
            try:
                game.max_guesses = int(max_guesses)
            except:
                game.max_guesses = None
        db.session.add(game)
        db.session.flush()  # get id
        for i, cat in enumerate(categories):
            c = Category(game_id=game.id, name=cat.get('name','').strip(), kind=cat.get('kind','list'), position=i)
            db.session.add(c)
        db.session.commit()
        return jsonify({"ok":True, "game_id": game.id})
    return render_template('create_game.html')

# --- Edit game (add list items, words, change settings) ---
def can_edit_game(game):
    return g.user and game.creator_id == g.user.id

@app.route('/game/<int:game_id>/edit', methods=['GET','POST'])
@login_required
def edit_game(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        flash('Not authorized', 'danger')
        return redirect(url_for('hub'))
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_category':
            cat_input = request.form.get('category_name', '').strip()
            added = 0
            if cat_input:
                # Parse multiline bulk input (Name:Type format)
                lines = [line.strip() for line in cat_input.split('\n') if line.strip()]
                for line in lines:
                    if ':' in line:
                        parts = line.split(':', 1)
                        cat_name = parts[0].strip()
                        cat_kind = parts[1].strip().lower()
                    else:
                        cat_name = line.strip()
                        cat_kind = 'list'
                    
                    if cat_name and cat_kind in ['list', 'date', 'number']:
                        pos = db.session.query(db.func.max(Category.position)).filter(Category.game_id == game.id).scalar() or 0
                        new_cat = Category(game_id=game.id, name=cat_name, kind=cat_kind, position=pos + 1)
                        db.session.add(new_cat)
                        added += 1
                db.session.commit()
                flash(f'Added {added} categor{"ies" if added != 1 else "y"}', 'success')
            return redirect(url_for('edit_game', game_id=game_id))
        elif action == 'delete_category':
            cid = int(request.form['category_id'])
            cat = Category.query.get_or_404(cid)
            if cat.game_id != game.id:
                flash('Not authorized', 'danger')
                return redirect(url_for('edit_game', game_id=game_id))
            db.session.delete(cat)
            db.session.commit()
            flash('Category deleted', 'success')
            return redirect(url_for('edit_game', game_id=game_id))
        elif action == 'add_list_item':
            cid = int(request.form['category_id'])
            val = request.form['value'].strip()
            if val:
                db.session.add(ListItem(category_id=cid, value=val))
                db.session.commit()
            return redirect(url_for('edit_game', game_id=game_id))
        elif action == 'delete_list_item':
            item_id = int(request.form['item_id'])
            item = ListItem.query.get_or_404(item_id)
            db.session.delete(item)
            db.session.commit()
            flash('Item deleted', 'success')
            return redirect(url_for('edit_game', game_id=game_id))
        elif action == 'add_word':
            name = request.form['word_name'].strip()
            # collect category values
            mapping = {}
            for c in game.categories:
                key = f"cat_{c.id}"
                if c.kind == 'list':
                    vals = request.form.getlist(key)
                    mapping[c.id] = [v for v in vals if v]
                else:
                    val = request.form.get(key)
                    mapping[c.id] = val if val else ''
            w = WordEntry(game_id=game.id, name=name, cat_values=serialize_cat_values(mapping))
            db.session.add(w)
            db.session.commit()
            flash(f'Word "{name}" added', 'success')
            return redirect(url_for('edit_game', game_id=game_id))
        elif action == 'delete_word':
            word_id = int(request.form['word_id'])
            word = WordEntry.query.get_or_404(word_id)
            if word.game_id != game.id:
                flash('Not authorized', 'danger')
                return redirect(url_for('edit_game', game_id=game_id))
            db.session.delete(word)
            db.session.commit()
            flash('Word deleted', 'success')
            return redirect(url_for('edit_game', game_id=game_id))
        elif action == 'update_settings':
            game.name = request.form.get('name', game.name)
            game.description = request.form.get('description', game.description)
            game.is_hidden = bool(request.form.get('is_hidden'))
            game.access = request.form.get('access','public')
            if game.access == 'link' and not game.link_token:
                game.link_token = secrets.token_hex(16)
            if request.form.get('max_guesses'):
                try:
                    game.max_guesses = int(request.form.get('max_guesses'))
                except:
                    game.max_guesses = None
            else:
                game.max_guesses = None
            db.session.commit()
            flash('Settings updated', 'success')
            return redirect(url_for('edit_game', game_id=game_id))
    # GET
    return render_template('edit_game.html', game=game)

# --- Play / Guessing ---
@app.route('/game/<int:game_id>/play', methods=['GET','POST'])
def play_game(game_id):
    game = Game.query.get_or_404(game_id)
    # visibility rules
    token = request.args.get('token')
    if game.is_hidden:
        if not (g.user and g.user.id == game.creator_id) and not (game.access=='link' and token==game.link_token):
            flash('Game not visible', 'danger')
            return redirect(url_for('hub'))
    if request.method == 'POST':
        # start or submit guess
        if request.form.get('action') == 'start':
            # pick secret word
            if not game.words:
                flash('Cannot play game with no words! The creator needs to add words first.', 'danger')
                return redirect(url_for('play_game', game_id=game_id))
            import random
            secret = random.choice(game.words)
            session_key = f"game_{game.id}_secret"
            session[f"{session_key}_word_id"] = secret.id
            session[f"{session_key}_attempts"] = 0
            session[f"{session_key}_history"] = []
            return redirect(url_for('play_game', game_id=game_id))
        elif request.form.get('action') == 'reset':
            # Reset game via AJAX
            if not game.words:
                return jsonify({'error': 'No words in game'}), 400
            import random
            secret = random.choice(game.words)
            session_key = f"game_{game.id}_secret"
            session[f"{session_key}_word_id"] = secret.id
            session[f"{session_key}_attempts"] = 0
            session[f"{session_key}_history"] = []
            return jsonify({'ok': True})
        else:
            # guess must be existing word (AJAX submission)
            try:
                guess_id = int(request.form.get('guess_word_id'))
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid word ID'}), 400
            
            session_key = f"game_{game.id}_secret"
            if f"{session_key}_word_id" not in session:
                return jsonify({'error': 'Start a new round first'}), 400
            
            secret_id = session[f"{session_key}_word_id"]
            secret = WordEntry.query.get(secret_id)
            guess = WordEntry.query.get(guess_id)
            if not (secret and guess):
                return jsonify({'error': 'Invalid guess'}), 400
            
            # compute feedback per category
            feedback_by_cat = {}  # dict: cat_id -> {result, guess_value}
            secret_vals = deserialize_cat_values(secret.cat_values)
            guess_vals = deserialize_cat_values(guess.cat_values)
            all_green = True
            for c in game.categories:
                s = secret_vals.get(c.id, [] if c.kind=='list' else '')
                gvals = guess_vals.get(c.id, [] if c.kind=='list' else '')
                if c.kind == 'list':
                    sset = set(s)
                    gset = set(gvals)
                    if not gset & sset:
                        result = 'wrong'
                        all_green = False
                    elif gset == sset:
                        result = 'correct'
                    else:
                        result = 'wrong'
                        all_green = False
                    display_val = ', '.join(list(gset)[:3]) if gset else '?'
                elif c.kind == 'date':
                    try:
                        sd = datetime.fromisoformat(s) if s else None
                        gd = datetime.fromisoformat(gvals) if gvals else None
                    except Exception:
                        sd = None; gd = None
                    if sd and gd:
                        if sd == gd:
                            result = 'correct'
                        elif gd < sd:
                            result = 'lower'
                            all_green = False
                        else:
                            result = 'higher'
                            all_green = False
                    else:
                        result = 'wrong'
                        all_green = False
                    display_val = gvals if gvals else '?'
                elif c.kind == 'number':
                    try:
                        s_num = int(s) if s else None
                        g_num = int(gvals) if gvals else None
                    except Exception:
                        s_num = None; g_num = None
                    if s_num is not None and g_num is not None:
                        if s_num == g_num:
                            result = 'correct'
                        elif g_num < s_num:
                            result = 'lower'
                            all_green = False
                        else:
                            result = 'higher'
                            all_green = False
                    else:
                        result = 'wrong'
                        all_green = False
                    display_val = str(g_num) if g_num is not None else '?'
                else:
                    result = 'wrong'
                    all_green = False
                    display_val = '?'
                feedback_by_cat[c.id] = {'result': result, 'guess_value': display_val}
            
            # update attempts
            session[f"{session_key}_attempts"] += 1
            attempts = session[f"{session_key}_attempts"]
            history = session.get(f"{session_key}_history", [])
            history.append({'guess_id': guess.id, 'guess_name': guess.name, 'feedback_by_cat': feedback_by_cat, 'is_correct': all_green})
            session[f"{session_key}_history"] = history
            
            # check win/limit
            won = all_green
            limit = game.max_guesses
            lost = False
            if limit and attempts >= limit and not won:
                lost = True
            
            # Return JSON response for AJAX
            return jsonify({
                'ok': True,
                'guess_name': guess.name,
                'feedback_by_cat': feedback_by_cat,
                'attempts': attempts,
                'won': won,
                'lost': lost,
                'categories': [{'id': c.id, 'name': c.name} for c in game.categories]
            })
            secret_vals = deserialize_cat_values(secret.cat_values)
            guess_vals = deserialize_cat_values(guess.cat_values)
            all_green = True
            for c in game.categories:
                s = secret_vals.get(c.id, [] if c.kind=='list' else '')
                gvals = guess_vals.get(c.id, [] if c.kind=='list' else '')
                if c.kind == 'list':
                    sset = set(s)
                    gset = set(gvals)
                    if not gset & sset:
                        result = 'wrong'
                        all_green = False
                    elif gset == sset:
                        result = 'correct'
                    else:
                        result = 'wrong'
                        all_green = False
                    # For display, show the first guessed item or join if multiple
                    display_val = ', '.join(list(gset)[:3]) if gset else '?'
                elif c.kind == 'date':
                    try:
                        sd = datetime.fromisoformat(s) if s else None
                        gd = datetime.fromisoformat(gvals) if gvals else None
                    except Exception:
                        sd = None; gd = None
                    if sd and gd:
                        if sd == gd:
                            result = 'correct'
                        elif gd < sd:
                            result = 'lower'
                            all_green = False
                        else:
                            result = 'higher'
                            all_green = False
                    else:
                        result = 'wrong'
                        all_green = False
                    display_val = gvals if gvals else '?'
                elif c.kind == 'number':
                    try:
                        s_num = int(s) if s else None
                        g_num = int(gvals) if gvals else None
                    except Exception:
                        s_num = None; g_num = None
                    if s_num is not None and g_num is not None:
                        if s_num == g_num:
                            result = 'correct'
                        elif g_num < s_num:
                            result = 'lower'
                            all_green = False
                        else:
                            result = 'higher'
                            all_green = False
                    else:
                        result = 'wrong'
                        all_green = False
                    display_val = str(g_num) if g_num is not None else '?'
                else:
                    result = 'wrong'
                    all_green = False
                    display_val = '?'
                feedback.append({'category': c.name, 'result': result})
                feedback_by_cat[c.id] = {'result': result, 'guess_value': display_val}
            # update attempts
            session[f"{session_key}_attempts"] += 1
            attempts = session[f"{session_key}_attempts"]
            history = session.get(f"{session_key}_history", [])
            history.append({'guess_id': guess.id, 'guess_name': guess.name, 'feedback': feedback, 'feedback_by_cat': feedback_by_cat, 'is_correct': all_green})
            session[f"{session_key}_history"] = history
            # check win/limit
            won = all_green
            limit = game.max_guesses
            lost = False
            if limit and attempts >= limit and not won:
                lost = True
            secret_values = deserialize_cat_values(secret.cat_values)
            return render_template('play_game.html', game=game, secret_word=secret.name, secret_values=secret_values,
                                   playing=True, attempts=attempts, history=history, won=won, lost=lost)
    # GET: show play page - check if there's an active game in session
    session_key = f"game_{game.id}_secret"
    playing = f"{session_key}_word_id" in session
    if playing:
        secret_id = session.get(f"{session_key}_word_id")
        secret = WordEntry.query.get(secret_id)
        attempts = session.get(f"{session_key}_attempts", 0)
        history = session.get(f"{session_key}_history", [])
        # Check win/loss state
        won = False
        lost = False
        if history:
            won = history[-1].get('is_correct', False)
            if game.max_guesses and attempts >= game.max_guesses and not won:
                lost = True
        
        # Pass secret word for end-game reveal
        secret_values = deserialize_cat_values(secret.cat_values) if secret else {}
        return render_template('play_game.html', game=game, secret_word=secret.name if secret else '?',
                               secret_values=secret_values, playing=True,
                               attempts=attempts, history=history, won=won, lost=lost)
    return render_template('play_game.html', game=game, playing=False)

# ---------- AJAX API ENDPOINTS ----------
@app.route('/api/game/<int:game_id>/add-category', methods=['POST'])
def api_add_category(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    data = request.get_json()
    name = data.get('name', '').strip()
    kind = data.get('kind', 'list')
    
    if not name or kind not in ['list', 'date', 'number']:
        return jsonify({'error': 'Invalid input'}), 400
    
    if len(game.categories) >= 6:
        return jsonify({'error': 'Max 6 categories'}), 400
    
    pos = db.session.query(db.func.max(Category.position)).filter(Category.game_id == game.id).scalar() or 0
    new_cat = Category(game_id=game.id, name=name, kind=kind, position=pos + 1)
    db.session.add(new_cat)
    db.session.commit()
    return jsonify({'success': True, 'category_id': new_cat.id})

@app.route('/api/game/<int:game_id>/delete-category/<int:cat_id>', methods=['POST'])
def api_delete_category(game_id, cat_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    cat = Category.query.get_or_404(cat_id)
    if cat.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403
    
    db.session.delete(cat)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/game/<int:game_id>/category/<int:cat_id>/items', methods=['GET'])
def api_get_category_items(game_id, cat_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    cat = Category.query.get_or_404(cat_id)
    if cat.game_id != game.id or cat.kind != 'list':
        return jsonify({'error': 'Invalid category'}), 400
    
    items = [{'id': item.id, 'value': item.value} for item in cat.list_items]
    return jsonify(items)

@app.route('/api/game/<int:game_id>/add-items', methods=['POST'])
def api_add_items(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    data = request.get_json()
    cat_id = data.get('categoryId')
    items = data.get('items', [])
    
    cat = Category.query.get_or_404(cat_id)
    if cat.game_id != game.id or cat.kind != 'list':
        return jsonify({'error': 'Invalid category'}), 400
    
    count = 0
    for item in items:
        if item.strip():
            db.session.add(ListItem(category_id=cat_id, value=item.strip()))
            count += 1
    db.session.commit()
    return jsonify({'success': True, 'count': count})

@app.route('/api/game/<int:game_id>/delete-item/<int:item_id>', methods=['POST'])
def api_delete_item(game_id, item_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    item = ListItem.query.get_or_404(item_id)
    cat = item.category
    if cat.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403
    
    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/game/<int:game_id>/add-word', methods=['POST'])
def api_add_word(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    data = request.get_json()
    name = data.get('name', '').strip()
    mapping = data.get('mapping', {})
    
    if not name:
        return jsonify({'error': 'Word name required'}), 400
    
    # Convert string keys to int
    mapping = {int(k): v for k, v in mapping.items()}
    
    word = WordEntry(game_id=game.id, name=name, cat_values=serialize_cat_values(mapping))
    db.session.add(word)
    db.session.commit()
    return jsonify({'success': True, 'word_id': word.id})

@app.route('/api/game/<int:game_id>/delete-word/<int:word_id>', methods=['POST'])
def api_delete_word(game_id, word_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    word = WordEntry.query.get_or_404(word_id)
    if word.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403
    
    db.session.delete(word)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/game/<int:game_id>/word/<int:word_id>')
def api_get_word(game_id, word_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    word = WordEntry.query.get_or_404(word_id)
    if word.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403
    
    cat_vals = deserialize_cat_values(word.cat_values)
    categories = []
    for cat in game.categories:
        categories.append({'id': cat.id, 'name': cat.name, 'kind': cat.kind})
    
    return jsonify({
        'id': word.id,
        'name': word.name,
        'values': cat_vals,
        'categories': categories
    })

@app.route('/api/game/<int:game_id>/update-word-order/<int:word_id>', methods=['POST'])
def api_update_word_order(game_id, word_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403
    
    word = WordEntry.query.get_or_404(word_id)
    if word.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403
    
    data = request.get_json()
    new_order = data.get('order', {})  # {cat_id: [ordered items]}
    
    # Get current values
    current_vals = deserialize_cat_values(word.cat_values)
    
    # Update with new order
    for cat_id_str, new_items in new_order.items():
        cat_id = int(cat_id_str)
        if cat_id in current_vals:
            current_vals[cat_id] = new_items
    
    word.cat_values = serialize_cat_values(current_vals)
    db.session.commit()
    return jsonify({'success': True})

# ---------- UTIL ----------
@app.cli.command('initdb')
def initdb():
    db.create_all()
    print("DB created.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)