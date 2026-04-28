"""
Реализация системы доступа.

Blueprint 'auth' отвечает за регистрацию, вход и выход пользователей.
Также экспортирует декоратор login_required для остальных модулей.
"""
from functools import wraps

from flask import (Blueprint, flash, g, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import generate_password_hash

from extensions import db
from models import User

auth_bp = Blueprint('auth', __name__)


# ── Декоратор ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapped


# ── Маршруты ─────────────────────────────────────────────────────────────────

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash('Provide username and password', 'danger')
            return redirect(url_for('auth.register'))
        if User.query.filter_by(username=username).first():
            flash('Username exists', 'danger')
            return redirect(url_for('auth.register'))
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('game.hub'))
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash('Invalid credentials', 'danger')
            return redirect(url_for('auth.login'))
        session['user_id'] = user.id
        return redirect(url_for('game.hub'))
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('game.hub'))
