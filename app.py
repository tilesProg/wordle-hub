"""
Точка входа приложения.

Создаёт Flask-приложение, настраивает конфигурацию,
регистрирует blueprint-ы и глобальный before_request.
"""
import os
import secrets

from dotenv import load_dotenv
from flask import Flask, g, redirect, session, url_for

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY']                  = os.getenv('SECRET_KEY') or secrets.token_hex(16)
app.config['SQLALCHEMY_DATABASE_URI']     = 'sqlite:///' + os.path.join(BASE_DIR, 'db.sqlite3')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from extensions import db
db.init_app(app)

from models import User
from auth import auth_bp
from constructor import constructor_bp
from game import game_bp

app.register_blueprint(auth_bp)
app.register_blueprint(constructor_bp)
app.register_blueprint(game_bp)


@app.before_request
def load_user():
    g.user = None
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])


@app.route('/')
def index():
    return redirect(url_for('game.hub'))


@app.cli.command('initdb')
def initdb():
    db.create_all()
    print('DB initialized.')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
