"""
Реализация модуля конструктора игр.

Blueprint 'constructor' содержит маршруты создания и редактирования игр,
а также все REST-подобные API-эндпоинты редактора.
"""
import secrets

from flask import (Blueprint, flash, g, jsonify,
                   redirect, render_template, request, url_for)
from sqlalchemy import func

from auth import login_required
from extensions import db
from models import Category, Game, ListItem, Word, WordValue, get_word_values_api, set_word_values

constructor_bp = Blueprint('constructor', __name__)


# ── Вспомогательная функция ──────────────────────────────────────────────────

def can_edit_game(game: Game) -> bool:
    return bool(g.user and game.creator_id == g.user.id)


# ── Создание игры ────────────────────────────────────────────────────────────

@constructor_bp.route('/game/create', methods=['GET', 'POST'])
@login_required
def create_game():
    if request.method == 'POST':
        data        = request.json
        name        = data.get('name', '').strip()
        description = data.get('description', '').strip()
        categories  = data.get('categories', [])
        access      = data.get('access', 'public')
        max_guesses = data.get('max_guesses') or None

        if not name or not categories:
            return jsonify({'ok': False, 'error': 'name and categories required'}), 400

        game = Game(name=name, description=description, creator_id=g.user.id, access=access)
        if access == 'link':
            game.link_token = secrets.token_hex(16)
        if max_guesses:
            try:
                game.max_guesses = int(max_guesses)
            except (ValueError, TypeError):
                game.max_guesses = None

        db.session.add(game)
        db.session.flush()

        for i, cat in enumerate(categories):
            db.session.add(Category(
                game_id=game.id,
                name=cat.get('name', '').strip(),
                kind=cat.get('kind', 'list'),
                position=i,
            ))

        db.session.commit()
        return jsonify({'ok': True, 'game_id': game.id})

    return render_template('create_game.html')


# ── Редактирование игры ──────────────────────────────────────────────────────

@constructor_bp.route('/game/<int:game_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_game(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        flash('Not authorized', 'danger')
        return redirect(url_for('game.hub'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_category':
            return _handle_add_category(game, game_id)
        elif action == 'delete_category':
            return _handle_delete_category(game, game_id)
        elif action == 'add_list_item':
            return _handle_add_list_item(game_id)
        elif action == 'delete_list_item':
            return _handle_delete_list_item(game_id)
        elif action == 'add_word':
            return _handle_add_word(game, game_id)
        elif action == 'delete_word':
            return _handle_delete_word(game, game_id)
        elif action == 'update_settings':
            return _handle_update_settings(game, game_id)

    return render_template('edit_game.html', game=game)


# ── Обработчики форм редактора ───────────────────────────────────────────────

def _handle_add_category(game, game_id):
    cat_input = request.form.get('category_name', '').strip()
    added = 0
    if cat_input:
        lines = [line.strip() for line in cat_input.split('\n') if line.strip()]
        for line in lines:
            if ':' in line:
                cat_name, cat_kind = line.split(':', 1)
                cat_name = cat_name.strip()
                cat_kind = cat_kind.strip().lower()
            else:
                cat_name = line
                cat_kind = 'list'
            if cat_name and cat_kind in ('list', 'date', 'number'):
                pos = db.session.query(func.max(Category.position)).filter(
                    Category.game_id == game.id
                ).scalar() or 0
                db.session.add(Category(game_id=game.id, name=cat_name, kind=cat_kind, position=pos + 1))
                added += 1
        db.session.commit()
        flash(f'Added {added} categor{"ies" if added != 1 else "y"}', 'success')
    return redirect(url_for('constructor.edit_game', game_id=game_id))


def _handle_delete_category(game, game_id):
    cat = Category.query.get_or_404(int(request.form['category_id']))
    if cat.game_id != game.id:
        flash('Not authorized', 'danger')
        return redirect(url_for('constructor.edit_game', game_id=game_id))
    db.session.delete(cat)
    db.session.commit()
    flash('Category deleted', 'success')
    return redirect(url_for('constructor.edit_game', game_id=game_id))


def _handle_add_list_item(game_id):
    cid = int(request.form['category_id'])
    val = request.form['value'].strip()
    if val:
        db.session.add(ListItem(category_id=cid, value=val))
        db.session.commit()
    return redirect(url_for('constructor.edit_game', game_id=game_id))


def _handle_delete_list_item(game_id):
    item = ListItem.query.get_or_404(int(request.form['item_id']))
    db.session.delete(item)
    db.session.commit()
    flash('Item deleted', 'success')
    return redirect(url_for('constructor.edit_game', game_id=game_id))


def _handle_add_word(game, game_id):
    name = request.form['word_name'].strip()
    mapping = {}
    for cat in game.categories:
        key = f'cat_{cat.id}'
        if cat.kind == 'list':
            vals = [v for v in request.form.getlist(key) if v]
            mapping[cat.id] = vals or ['Not specified']
        else:
            val = request.form.get(key, '').strip()
            mapping[cat.id] = val or 'Not specified'
    word = Word(game_id=game.id, name=name)
    db.session.add(word)
    db.session.flush()
    set_word_values(word, game.categories, mapping)
    db.session.commit()
    flash(f'Word "{name}" added', 'success')
    return redirect(url_for('constructor.edit_game', game_id=game_id))


def _handle_delete_word(game, game_id):
    word = Word.query.get_or_404(int(request.form['word_id']))
    if word.game_id != game.id:
        flash('Not authorized', 'danger')
        return redirect(url_for('constructor.edit_game', game_id=game_id))
    db.session.delete(word)
    db.session.commit()
    flash('Word deleted', 'success')
    return redirect(url_for('constructor.edit_game', game_id=game_id))


def _handle_update_settings(game, game_id):
    game.name        = request.form.get('name', game.name)
    game.description = request.form.get('description', game.description)
    game.is_hidden   = bool(request.form.get('is_hidden'))
    game.access      = request.form.get('access', 'public')
    if game.access == 'link' and not game.link_token:
        game.link_token = secrets.token_hex(16)
    raw_guesses = request.form.get('max_guesses')
    try:
        game.max_guesses = int(raw_guesses) if raw_guesses else None
    except (ValueError, TypeError):
        game.max_guesses = None
    db.session.commit()
    flash('Settings updated', 'success')
    return redirect(url_for('constructor.edit_game', game_id=game_id))


# ── API-эндпоинты редактора ──────────────────────────────────────────────────

@constructor_bp.route('/api/game/<int:game_id>/add-category', methods=['POST'])
def api_add_category(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    data = request.get_json()
    name = data.get('name', '').strip()
    kind = data.get('kind', 'list')

    if not name or kind not in ('list', 'date', 'number'):
        return jsonify({'error': 'Invalid input'}), 400
    if len(game.categories) >= 6:
        return jsonify({'error': 'Max 6 categories'}), 400

    pos = db.session.query(func.max(Category.position)).filter(
        Category.game_id == game.id
    ).scalar() or 0
    cat = Category(game_id=game.id, name=name, kind=kind, position=pos + 1)
    db.session.add(cat)
    db.session.commit()
    return jsonify({'success': True, 'category_id': cat.id})


@constructor_bp.route('/api/game/<int:game_id>/delete-category/<int:cat_id>', methods=['POST'])
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


@constructor_bp.route('/api/game/<int:game_id>/category/<int:cat_id>/items')
def api_get_category_items(game_id, cat_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    cat = Category.query.get_or_404(cat_id)
    if cat.game_id != game.id or cat.kind != 'list':
        return jsonify({'error': 'Invalid category'}), 400

    return jsonify([{'id': item.id, 'value': item.value} for item in cat.list_items])


@constructor_bp.route('/api/game/<int:game_id>/add-items', methods=['POST'])
def api_add_items(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    data   = request.get_json()
    cat_id = data.get('categoryId')
    items  = data.get('items', [])

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


@constructor_bp.route('/api/game/<int:game_id>/delete-item/<int:item_id>', methods=['POST'])
def api_delete_item(game_id, item_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    item = ListItem.query.get_or_404(item_id)
    if item.category.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403

    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})


@constructor_bp.route('/api/game/<int:game_id>/add-word', methods=['POST'])
def api_add_word(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    data        = request.get_json()
    name        = data.get('name', '').strip()
    raw_mapping = data.get('mapping', {})

    if not name:
        return jsonify({'error': 'Word name required'}), 400

    mapping = {int(k): v for k, v in raw_mapping.items()}
    full_mapping = {}
    for cat in game.categories:
        val = mapping.get(cat.id)
        if cat.kind == 'list':
            full_mapping[cat.id] = val if val else ['Not specified']
        else:
            full_mapping[cat.id] = val if val else 'Not specified'

    word = Word(game_id=game.id, name=name)
    db.session.add(word)
    db.session.flush()
    set_word_values(word, game.categories, full_mapping)
    db.session.commit()
    return jsonify({'success': True, 'word_id': word.id})


@constructor_bp.route('/api/game/<int:game_id>/delete-word/<int:word_id>', methods=['POST'])
def api_delete_word(game_id, word_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    word = Word.query.get_or_404(word_id)
    if word.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403

    db.session.delete(word)
    db.session.commit()
    return jsonify({'success': True})


@constructor_bp.route('/api/game/<int:game_id>/word/<int:word_id>')
def api_get_word(game_id, word_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    word = Word.query.get_or_404(word_id)
    if word.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403

    return jsonify({
        'id':         word.id,
        'name':       word.name,
        'values':     get_word_values_api(word, game.categories),
        'categories': [{'id': c.id, 'name': c.name, 'kind': c.kind} for c in game.categories],
    })


@constructor_bp.route('/api/game/<int:game_id>/update-word-order/<int:word_id>', methods=['POST'])
def api_update_word_order(game_id, word_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    word = Word.query.get_or_404(word_id)
    if word.game_id != game.id:
        return jsonify({'error': 'Not authorized'}), 403

    data      = request.get_json()
    new_order = data.get('order', {})  # {str(cat_id): [ordered_items]}
    cat_map   = {cat.id: cat for cat in game.categories}

    for cat_id_str, ordered_items in new_order.items():
        cat_id = int(cat_id_str)
        cat    = cat_map.get(cat_id)
        if cat and cat.kind == 'list':
            WordValue.query.filter_by(word_id=word.id, category_id=cat_id).delete()
            for item in ordered_items:
                if item:
                    db.session.add(WordValue(word_id=word.id, category_id=cat_id, value_group=str(item)))

    db.session.commit()
    return jsonify({'success': True})


@constructor_bp.route('/api/game/<int:game_id>/delete', methods=['POST'])
def api_delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    if not can_edit_game(game):
        return jsonify({'error': 'Not authorized'}), 403

    db.session.delete(game)
    db.session.commit()
    return jsonify({'success': True})
