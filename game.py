"""
Реализация игрового модуля.

Blueprint 'game' содержит хаб со списком игр и страницу прохождения игры.
"""
import random
from datetime import datetime

from flask import (Blueprint, flash, g, jsonify,
                   redirect, render_template, request, session, url_for)

from models import Game, Word, WordValue, get_word_values

game_bp = Blueprint('game', __name__)


# ── Игровая логика ────────────────────────────────────────────────────────────

def _compute_feedback(secret_vals: dict, guess_vals: dict, categories: list) -> tuple[dict, bool]:
    """Вычисляет обратную связь по каждой категории.

    Возвращает (feedback_by_cat, all_green), где:
      feedback_by_cat = {cat_id: {'result': str, 'guess_value': str}}
      all_green       = True, если угадано верно по всем категориям
    """
    feedback   = {}
    all_green  = True

    for cat in categories:
        s    = secret_vals.get(cat.id)
        g_val = guess_vals.get(cat.id)

        if cat.kind == 'list':
            sset = {v for v in (s or []) if v and v != 'Not specified'}
            gset = {v for v in (g_val or []) if v and v != 'Not specified'}

            if not sset and not gset:
                result = 'correct'
            elif gset == sset:
                result = 'correct'
            elif gset & sset:
                result = 'partial'
                all_green = False
            else:
                result = 'wrong'
                all_green = False

            display = ', '.join(list(gset)[:3]) if gset else 'Not specified'

        elif cat.kind == 'date':
            sd, gd = s, g_val   # datetime | None
            if sd is None and gd is None:
                result = 'correct'
            elif sd and gd:
                if gd == sd:
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
            display = gd.isoformat() if gd else 'Not specified'

        elif cat.kind == 'number':
            sn, gn = s, g_val   # float | None
            if sn is None and gn is None:
                result = 'correct'
            elif sn is not None and gn is not None:
                if gn == sn:
                    result = 'correct'
                elif gn < sn:
                    result = 'lower'
                    all_green = False
                else:
                    result = 'higher'
                    all_green = False
            else:
                result = 'wrong'
                all_green = False
            if gn is not None:
                display = str(int(gn) if gn == int(gn) else gn)
            else:
                display = 'Not specified'

        else:
            result    = 'wrong'
            all_green = False
            display   = '?'

        feedback[cat.id] = {'result': result, 'guess_value': display}

    return feedback, all_green


def _build_display_values(word: Word, categories: list) -> dict:
    """Возвращает {cat_id: list | str} для финального раскрытия ответа в шаблоне."""
    by_cat: dict[int, list] = {}
    for wv in word.values:
        by_cat.setdefault(wv.category_id, []).append(wv)

    result = {}
    for cat in categories:
        wvs = by_cat.get(cat.id, [])
        if cat.kind == 'list':
            vals = [wv.value_group for wv in wvs if wv.value_group]
            result[cat.id] = vals or ['Not specified']
        elif cat.kind == 'date':
            wv = wvs[0] if wvs else None
            result[cat.id] = [wv.value_date.isoformat() if (wv and wv.value_date) else 'Not specified']
        elif cat.kind == 'number':
            wv = wvs[0] if wvs else None
            if wv and wv.value_number is not None:
                n = wv.value_number
                result[cat.id] = [str(int(n) if n == int(n) else n)]
            else:
                result[cat.id] = ['Not specified']
    return result


# ── Маршруты ─────────────────────────────────────────────────────────────────

@game_bp.route('/hub')
def hub():
    games  = Game.query.order_by(Game.id.desc()).all()
    token  = request.args.get('token')
    visible = []
    for game in games:
        if game.is_hidden:
            if g.user and g.user.id == game.creator_id:
                visible.append(game)
            elif game.access == 'link' and token and token == game.link_token:
                visible.append(game)
        else:
            if game.access == 'private' and not (g.user and g.user.id == game.creator_id):
                continue
            visible.append(game)
    return render_template('hub.html', games=visible)


@game_bp.route('/game/<int:game_id>/play', methods=['GET', 'POST'])
def play_game(game_id):
    game        = Game.query.get_or_404(game_id)
    token       = request.args.get('token')
    session_key = f'game_{game.id}_secret'

    if game.is_hidden:
        allowed = (g.user and g.user.id == game.creator_id) or \
                  (game.access == 'link' and token == game.link_token)
        if not allowed:
            flash('Game not visible', 'danger')
            return redirect(url_for('game.hub'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'start':
            if not game.words:
                flash('Cannot play game with no words! The creator needs to add words first.', 'danger')
                return redirect(url_for('game.play_game', game_id=game_id))
            secret = random.choice(game.words)
            session[f'{session_key}_word_id']  = secret.id
            session[f'{session_key}_attempts'] = 0
            session[f'{session_key}_history']  = []
            return redirect(url_for('game.play_game', game_id=game_id))

        elif action == 'reset':
            if not game.words:
                return jsonify({'error': 'No words in game'}), 400
            secret = random.choice(game.words)
            session[f'{session_key}_word_id']  = secret.id
            session[f'{session_key}_attempts'] = 0
            session[f'{session_key}_history']  = []
            return jsonify({'ok': True})

        else:  # guess
            try:
                guess_id = int(request.form.get('guess_word_id'))
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid word ID'}), 400

            if f'{session_key}_word_id' not in session:
                return jsonify({'error': 'Start a new round first'}), 400

            secret = Word.query.get(session[f'{session_key}_word_id'])
            guess  = Word.query.get(guess_id)
            if not (secret and guess):
                return jsonify({'error': 'Invalid guess'}), 400

            secret_vals          = get_word_values(secret, game.categories)
            guess_vals           = get_word_values(guess, game.categories)
            feedback, all_green  = _compute_feedback(secret_vals, guess_vals, game.categories)

            session[f'{session_key}_attempts'] += 1
            attempts = session[f'{session_key}_attempts']
            history  = session.get(f'{session_key}_history', [])
            history.append({
                'guess_id':       guess.id,
                'guess_name':     guess.name,
                'feedback_by_cat': feedback,
                'is_correct':     all_green,
            })
            session[f'{session_key}_history'] = history

            won  = all_green
            lost = bool(game.max_guesses and attempts >= game.max_guesses and not won)

            return jsonify({
                'ok':             True,
                'guess_name':     guess.name,
                'feedback_by_cat': feedback,
                'attempts':       attempts,
                'won':            won,
                'lost':           lost,
                'categories':     [{'id': c.id, 'name': c.name} for c in game.categories],
            })

    # GET
    playing = f'{session_key}_word_id' in session
    if not playing:
        return render_template('play_game.html', game=game, playing=False)

    secret   = Word.query.get(session[f'{session_key}_word_id'])
    attempts = session.get(f'{session_key}_attempts', 0)
    history  = session.get(f'{session_key}_history', [])
    won      = bool(history and history[-1].get('is_correct'))
    lost     = bool(game.max_guesses and attempts >= game.max_guesses and not won)

    secret_values = _build_display_values(secret, game.categories) if secret else {}

    return render_template(
        'play_game.html',
        game=game,
        secret_word=secret.name if secret else '?',
        secret_values=secret_values,
        playing=True,
        attempts=attempts,
        history=history,
        won=won,
        lost=lost,
    )
